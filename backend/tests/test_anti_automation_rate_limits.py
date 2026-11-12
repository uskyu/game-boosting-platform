"""反自动化限流测试（2026-11-11）。

覆盖：
1. 通用滑动窗口服务本身；
2. 大厅列表 / 摘要 / 接单验证码 / 接单尝试的 429 与 Retry-After；
3. 关闭限流开关（测试默认）时不影响其他用例。

这些用例显式请求 ``rate_limits_enabled``，因为 conftest 默认把
``RATE_LIMIT_ENABLED`` 设为 False，避免历史用例连续请求被误伤。
"""

import asyncio

from sqlalchemy import select

from app.api.endpoints import orders as orders_endpoints
from app.core.config import settings
from app.models.site_setting import SiteSetting
from app.services import captcha_service, rate_limit_service
from tests.conftest import auth_header


async def _enable_accept_captcha(db_session) -> None:
    row = (
        await db_session.execute(select(SiteSetting).where(SiteSetting.id == 1))
    ).scalar_one()
    row.accept_captcha_enabled = True
    await db_session.commit()


async def _create_order(client, publisher) -> dict:
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "title": "限流测试订单",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "200.00",
            "max_claims": 1,
        },
        headers=auth_header(publisher),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_sliding_window_service():
    """同一 key 窗口内超过 limit 立即拒绝，retry_after 为正数。"""
    rate_limit_service.reset()
    allowed, retry_after = rate_limit_service.check("unit:test", 1, 1.0)
    assert allowed is True
    assert retry_after == 0

    blocked, retry_after = rate_limit_service.check("unit:test", 1, 1.0)
    assert blocked is False
    assert retry_after >= 1
    rate_limit_service.reset()


async def test_hall_slim_list_rate_limited(client, booster_user, rate_limits_enabled):
    """大厅瘦身列表 1 次/1.5 秒：脚本 0.8 秒一轮的第二发直接 429。"""
    headers = auth_header(booster_user)
    first = await client.get(
        "/orders/?page=1&pageSize=100&slim=1", headers=headers
    )
    assert first.status_code == 200, first.text

    second = await client.get(
        "/orders/?page=1&pageSize=100&slim=1", headers=headers
    )
    assert second.status_code == 429
    detail = second.json()["detail"]
    assert detail["code"] == "orders_rate_limited"
    assert detail["retry_after"] >= 1
    assert second.headers["Retry-After"] == str(detail["retry_after"])


async def test_hall_bulk_list_without_slim_rate_limited(
    client, booster_user, rate_limits_enabled
):
    """脚本去掉 slim 但保留 pageSize=100 批量抓取，仍走严格桶。"""
    headers = auth_header(booster_user)
    first = await client.get(
        "/orders/?page=1&page_size=100", headers=headers
    )
    assert first.status_code == 200, first.text

    second = await client.get(
        "/orders/?page=1&page_size=100", headers=headers
    )
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "orders_rate_limited"


async def test_hall_digest_rate_limited(client, booster_user, rate_limits_enabled):
    """摘要 1 次/秒：脚本 0.25 秒一轮会被 429。"""
    headers = auth_header(booster_user)
    first = await client.get("/orders/hall-digest", headers=headers)
    assert first.status_code == 200, first.text

    second = await client.get("/orders/hall-digest", headers=headers)
    assert second.status_code == 429
    detail = second.json()["detail"]
    assert detail["code"] == "hall_digest_rate_limited"
    assert detail["retry_after"] >= 1


async def test_accept_captcha_burst_rate_limited(
    client, booster_user, admin_user, db_session, rate_limits_enabled
):
    """验证码并发预取：第一张 200，紧接着第二张 429（脚本 3 张并发的场景）。"""
    await _enable_accept_captcha(db_session)
    order = await _create_order(client, admin_user)
    headers = auth_header(booster_user)

    first = await client.get(
        f"/orders/accept-captcha?order_id={order['id']}", headers=headers
    )
    assert first.status_code == 200, first.text

    second = await client.get(
        f"/orders/accept-captcha?order_id={order['id']}", headers=headers
    )
    assert second.status_code == 429
    detail = second.json()["detail"]
    assert detail["code"] == "captcha_rate_limited"
    assert detail["retry_after"] >= 1


async def test_accept_captcha_parallel_prefetch_only_one_succeeds(
    client, booster_user, admin_user, db_session, rate_limits_enabled
):
    """模拟抢单器 Promise.all 并发预取 3 张码：只有第 1 张 200，其余 429。"""
    await _enable_accept_captcha(db_session)
    order = await _create_order(client, admin_user)
    headers = auth_header(booster_user)

    responses = await asyncio.gather(
        *[
            client.get(
                f"/orders/accept-captcha?order_id={order['id']}", headers=headers
            )
            for _ in range(3)
        ]
    )
    statuses = [resp.status_code for resp in responses]
    assert statuses.count(200) == 1, statuses
    assert statuses.count(429) == 2, statuses


async def test_accept_burst_rate_limited(
    client, booster_user, admin_user, db_session, rate_limits_enabled
):
    """接单尝试 1 次/秒：第一发（验证码错）400，紧接着第二发 429。"""
    await _enable_accept_captcha(db_session)
    order = await _create_order(client, admin_user)
    headers = auth_header(booster_user)

    captcha = await client.get(
        f"/orders/accept-captcha?order_id={order['id']}", headers=headers
    )
    assert captcha.status_code == 200, captcha.text
    captcha_id = captcha.json()["captcha_id"]
    wrong = {"captcha_id": captcha_id, "captcha_code": "wrong"}

    first = await client.put(
        f"/orders/{order['id']}/accept", headers=headers, json=wrong
    )
    assert first.status_code == 400
    assert first.json()["detail"]["code"] == "captcha_invalid"

    second = await client.put(
        f"/orders/{order['id']}/accept", headers=headers, json=wrong
    )
    assert second.status_code == 429
    detail = second.json()["detail"]
    assert detail["code"] == "accept_rate_limited"
    assert detail["retry_after"] >= 1


async def test_per_order_accept_limit(
    client, booster_user, admin_user, db_session, rate_limits_enabled, monkeypatch
):
    """同一用户同一订单的尝试上限独立生效（防反复换码硬试）。"""
    await _enable_accept_captcha(db_session)
    order = await _create_order(client, admin_user)
    headers = auth_header(booster_user)

    # 放宽用户级 1 次/秒，单独验证“同订单 1 次/10 分钟”的键是否接上
    monkeypatch.setattr(orders_endpoints, "_ACCEPT_BURST_RATE", (100, 1.0))
    monkeypatch.setattr(orders_endpoints, "_ACCEPT_PER_ORDER_RATE", (1, 600.0))
    captcha = await client.get(
        f"/orders/accept-captcha?order_id={order['id']}", headers=headers
    )
    assert captcha.status_code == 200, captcha.text
    captcha_id = captcha.json()["captcha_id"]
    wrong = {"captcha_id": captcha_id, "captcha_code": "wrong"}

    first = await client.put(
        f"/orders/{order['id']}/accept", headers=headers, json=wrong
    )
    assert first.status_code == 400

    second = await client.put(
        f"/orders/{order['id']}/accept", headers=headers, json=wrong
    )
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "accept_rate_limited"


async def test_rate_limit_disabled_by_default(client, booster_user):
    """conftest 默认关闭限流：连续两次摘要都 200，不影响既有用例。"""
    assert settings.RATE_LIMIT_ENABLED is False
    headers = auth_header(booster_user)
    first = await client.get("/orders/hall-digest", headers=headers)
    second = await client.get("/orders/hall-digest", headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
