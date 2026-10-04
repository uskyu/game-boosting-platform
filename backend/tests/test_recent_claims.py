"""大厅「今日已接单」区块接口测试（GET /orders/recent-claims）。

覆盖：登录要求、字段完整、按接单时间（claim id）倒序、limit/翻页生效，
以及「今天」口径（昨天的接单记录不出现）。
"""

from datetime import timedelta
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import text, update

from app.models.order import OrderClaim
from tests.conftest import auth_header


async def _register(client: AsyncClient, email: str, username: str) -> dict:
    from app.services import captcha_service

    captcha_id, _ = captcha_service.create()
    code, _ = captcha_service._store[captcha_id]
    resp = await client.post(
        "/auth/register",
        json={"email": email, "username": username, "password": "Passw0rd123",
              "captcha_id": captcha_id, "captcha_code": code},
    )
    assert resp.status_code in (200, 201)
    return resp.json()


async def _create_order(client: AsyncClient, admin_user: dict, max_claims: int = 1) -> dict:
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "title": "王者荣耀 钻石上王者",
            "intro": "微信区，三天内完成，包售后",
            "price": "200.00",
            "max_claims": max_claims,
        },
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201
    return resp.json()


def _recent_claim_ids(data: dict) -> list[int]:
    return [item["id"] for item in data["items"]]


async def test_recent_claims_requires_login(client: AsyncClient):
    """未登录 401；登录后可用（空态返回 total=0、items=[]）。"""
    resp = await client.get("/orders/recent-claims")
    assert resp.status_code == 401


async def test_recent_claims_empty_when_no_claims_today(client: AsyncClient, registered_user: dict):
    resp = await client.get("/orders/recent-claims", headers=auth_header(registered_user))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


async def test_recent_claims_fields_and_newest_first(
    client: AsyncClient, admin_user: dict
):
    """字段完整；按接单时间倒序（后接单的排在前）。"""
    order = await _create_order(client, admin_user, max_claims=2)
    booster_a = await _register(client, "rca@example.com", "ClaimerA")
    booster_b = await _register(client, "rcb@example.com", "ClaimerB")

    # A 先接、B 后接：B 的 claim id 更大（倒序时 B 在前）
    resp = await client.put(f"/orders/{order['id']}/accept", headers=auth_header(booster_a))
    assert resp.status_code == 200
    resp = await client.put(f"/orders/{order['id']}/accept", headers=auth_header(booster_b))
    assert resp.status_code == 200

    resp = await client.get("/orders/recent-claims", headers=auth_header(booster_a))
    assert resp.status_code == 200
    data = resp.json()

    assert data["total"] == 2
    assert len(data["items"]) == 2
    assert data["items"][0]["id"] > data["items"][1]["id"]

    latest = data["items"][0]
    # —— 字段完整：claim / order / booster / 保证金 ——
    assert latest["booster"]["id"] == booster_b["user"]["id"]
    assert latest["booster"]["username"] == "ClaimerB"
    assert latest["booster"]["total_completed"] == 0
    assert Decimal(str(latest["deposit_balance"])) == Decimal("0")

    assert latest["order"]["id"] == order["id"]
    assert latest["order"]["title"] == "王者荣耀 钻石上王者"
    assert latest["order"]["intro"] == "微信区，三天内完成，包售后"
    assert Decimal(str(latest["order"]["price"])) == Decimal("200.00")

    # 接单时间：ISO 8601 UTC 字符串
    assert isinstance(latest["created_at"], str)
    assert latest["created_at"].endswith("Z")

    # 另一条是 A
    earlier = data["items"][1]
    assert earlier["booster"]["username"] == "ClaimerA"


async def test_recent_claims_limit_and_paging(client: AsyncClient, admin_user: dict):
    """limit 生效（超出上限 422）；offset / page 翻页拿到剩余记录。"""
    order = await _create_order(client, admin_user, max_claims=3)
    for index in range(3):
        booster = await _register(client, f"lim{index}@example.com", f"Lim{index}")
        resp = await client.put(f"/orders/{order['id']}/accept", headers=auth_header(booster))
        assert resp.status_code == 200

    resp = await client.get("/orders/recent-claims?limit=2", headers=auth_header(admin_user))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert len(data["items"]) == 2

    # limit 超过上限 100 → 422
    resp = await client.get("/orders/recent-claims?limit=101", headers=auth_header(admin_user))
    assert resp.status_code == 422

    # offset 翻页：第 3 条
    resp = await client.get("/orders/recent-claims?limit=2&offset=2", headers=auth_header(admin_user))
    assert resp.status_code == 200
    assert [item["id"] for item in resp.json()["items"]] == [data["items"][0]["id"] - 2]

    # page 翻页与 offset 等价（page=2, limit=2 → offset=2）
    resp = await client.get("/orders/recent-claims?limit=2&page=2", headers=auth_header(admin_user))
    assert resp.status_code == 200
    assert _recent_claim_ids(resp.json()) == [data["items"][0]["id"] - 2]


async def test_recent_claims_excludes_yesterday(
    client: AsyncClient, admin_user: dict, db_session
):
    """口径：只统计「今天」接的单，昨天的接单记录不出现。"""
    order = await _create_order(client, admin_user)
    booster = await _register(client, "yday@example.com", "YesterdayClaimer")
    resp = await client.put(f"/orders/{order['id']}/accept", headers=auth_header(booster))
    assert resp.status_code == 200

    # 在库内把该 claim 的接单时间改到昨天（与 MySQL NOW() 同源，避免时区口径分叉）
    await db_session.execute(
        update(OrderClaim)
        .where(OrderClaim.order_id == order["id"])
        .values(created_at=text("NOW() - INTERVAL 1 DAY"))
    )
    await db_session.commit()

    resp = await client.get("/orders/recent-claims", headers=auth_header(admin_user))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


async def test_recent_claims_scope_defaults_to_today(client: AsyncClient, admin_user: dict):
    """scope 缺省即 today；显式传 scope=today 等价；其他值 422。"""
    order = await _create_order(client, admin_user)
    booster = await _register(client, "scope@example.com", "ScopeClaimer")
    resp = await client.put(f"/orders/{order['id']}/accept", headers=auth_header(booster))
    assert resp.status_code == 200

    resp = await client.get("/orders/recent-claims", headers=auth_header(admin_user))
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    resp = await client.get(
        "/orders/recent-claims?scope=today", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    resp = await client.get(
        "/orders/recent-claims?scope=week", headers=auth_header(admin_user)
    )
    assert resp.status_code == 422
