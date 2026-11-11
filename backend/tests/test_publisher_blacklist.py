"""发单人黑名单：接口契约、接单拦截、列表/详情标记与通知过滤。

口径（2026-11-11 冻结）：
- 平表账号级黑名单，只影响「接该发单人的单」，大厅/详情仍可见；
- lookup 只做用户名/登录邮箱完全匹配，最多返回 1 人，不回显邮箱；
- 被拉黑接单返回 403，文案固定「你已被该发布人拉黑，无法接此单」。
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.api.notification_utils import notify_boosters_new_order
from app.models.deposit import DepositSetting, DepositTier
from app.models.notification import Notification
from app.models.order import Order
from app.models.publisher_blacklist import PublisherBlacklist
from app.models.user import User
from app.models.wallet import Wallet
from app.services.blacklist_service import (
    ACCEPT_BLOCKED_DETAIL,
    LIMIT_EXCEEDED_DETAIL,
    PUBLISHER_ONLY_DETAIL,
    TARGET_FORBIDDEN_DETAIL,
    TARGET_NOT_FOUND_DETAIL,
)
from app.services import captcha_service
from tests.conftest import auth_header

PASSWORD = "BlacklistPass1"
ITEM_KEYS = {"id", "blocked_user_id", "username", "avatar_url", "reason", "created_at"}


@pytest.fixture(autouse=True)
def _clear_rate_buckets():
    """限流桶挂在进程内存里，按用户 ID 计数；测试自增 ID 会复位，逐用例清空。"""
    captcha_service._rate.clear()
    yield
    captcha_service._rate.clear()


async def _register(
    client: AsyncClient,
    make_captcha,
    email: str,
    username: str,
) -> dict:
    resp = await client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "password": PASSWORD,
            **make_captcha(),
        },
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _set_can_publish(db_session, user_id: int, value: bool) -> None:
    result = await db_session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()
    user.can_publish = value
    await db_session.commit()


async def _fund(client: AsyncClient, admin_user: dict, user: dict, amount: str) -> None:
    resp = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": amount, "reason": "测试充值"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code in (200, 201), resp.text


async def _create_order(client: AsyncClient, publisher: dict, price: str = "100.00") -> dict:
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "title": "黑名单测试订单",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": price,
            "boss_contact": "B0001",
            "max_claims": 1,
        },
        headers=auth_header(publisher),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _accept(client: AsyncClient, order_id: int, user: dict):
    return await client.put(
        f"/orders/{order_id}/accept", headers=auth_header(user)
    )


async def _add_blacklist(
    client: AsyncClient,
    publisher: dict,
    user_id: int,
    reason: str | None = None,
):
    body = {"user_id": user_id}
    if reason is not None:
        body["reason"] = reason
    return await client.post(
        "/blacklist", json=body, headers=auth_header(publisher)
    )


# ---------------------------------------------------------------------------
# 拉黑 / 解除 / 幂等 / 上限 / 权限
# ---------------------------------------------------------------------------


async def test_add_list_and_remove_blacklist(client: AsyncClient, make_captcha):
    publisher = await _register(client, make_captcha, "bl.pub@example.com", "BlPub")
    target = await _register(client, make_captcha, "bl.target@example.com", "BlTarget")

    resp = await _add_blacklist(
        client, publisher, target["user"]["id"], reason="  备注  "
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()
    assert item["blocked_user_id"] == target["user"]["id"]
    assert item["username"] == "BlTarget"
    assert item["reason"] == "备注"  # 首尾空白清理
    assert item["created_at"].endswith("Z")
    assert set(item) == ITEM_KEYS

    mine = await client.get("/blacklist/mine", headers=auth_header(publisher))
    assert mine.status_code == 200
    payload = mine.json()
    assert payload["total"] == 1
    assert payload["page"] == 1 and payload["page_size"] == 20 and payload["pages"] == 1
    assert set(payload["items"][0]) == ITEM_KEYS
    assert "email" not in payload["items"][0]

    # q 只在「我拉黑的人」里按用户名过滤
    filtered = await client.get(
        "/blacklist/mine", params={"q": "BlTar"}, headers=auth_header(publisher)
    )
    assert filtered.json()["total"] == 1
    other = await client.get(
        "/blacklist/mine", params={"q": "Nobody"}, headers=auth_header(publisher)
    )
    assert other.json()["total"] == 0

    resp = await client.delete(
        f"/blacklist/{target['user']['id']}", headers=auth_header(publisher)
    )
    assert resp.status_code == 204
    # 未拉黑也 204（幂等）
    resp = await client.delete(
        f"/blacklist/{target['user']['id']}", headers=auth_header(publisher)
    )
    assert resp.status_code == 204

    mine = await client.get("/blacklist/mine", headers=auth_header(publisher))
    assert mine.json()["total"] == 0


async def test_duplicate_blacklist_is_idempotent(client: AsyncClient, make_captcha):
    publisher = await _register(client, make_captcha, "dup.pub@example.com", "DupPub")
    target = await _register(client, make_captcha, "dup.target@example.com", "DupTarget")

    first = await _add_blacklist(
        client, publisher, target["user"]["id"], reason="第一次"
    )
    assert first.status_code == 200
    second = await _add_blacklist(
        client, publisher, target["user"]["id"], reason="第二次"
    )
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["reason"] == "第一次"

    mine = await client.get("/blacklist/mine", headers=auth_header(publisher))
    assert mine.json()["total"] == 1


async def test_cannot_blacklist_self_admin_or_disabled(
    client: AsyncClient, admin_user: dict, db_session, make_captcha
):
    publisher = await _register(client, make_captcha, "self.pub@example.com", "SelfPub")

    resp = await _add_blacklist(client, publisher, publisher["user"]["id"])
    assert resp.status_code == 400
    assert resp.json()["detail"] == TARGET_FORBIDDEN_DETAIL

    resp = await _add_blacklist(client, publisher, admin_user["user"]["id"])
    assert resp.status_code == 400
    assert resp.json()["detail"] == TARGET_FORBIDDEN_DETAIL

    # 目标必须启用
    disabled = await _register(
        client, make_captcha, "self.disabled@example.com", "SelfDisabled"
    )
    result = await db_session.execute(
        select(User).where(User.id == disabled["user"]["id"])
    )
    disabled_row = result.scalar_one()
    disabled_row.is_active = False
    await db_session.commit()
    resp = await _add_blacklist(client, publisher, disabled["user"]["id"])
    assert resp.status_code == 400
    assert resp.json()["detail"] == TARGET_FORBIDDEN_DETAIL

    # lookup 命中自己/管理员同样 400
    lookup_self = await client.get(
        "/blacklist/lookup",
        params={"q": "SelfPub"},
        headers=auth_header(publisher),
    )
    assert lookup_self.status_code == 400
    lookup_admin = await client.get(
        "/blacklist/lookup",
        params={"q": "TestAdmin"},
        headers=auth_header(publisher),
    )
    assert lookup_admin.status_code == 400


async def test_blacklist_limit_500(client: AsyncClient, db_session, make_captcha):
    publisher = await _register(client, make_captcha, "limit.pub@example.com", "LimitPub")

    # 直接批量造 501 个目标用户 + 500 条黑名单，验证第 501 条被拒
    targets = [
        User(
            email=f"bulk-{index}@example.com",
            username=f"BulkTarget{index}",
            hashed_password="not-used",
        )
        for index in range(501)
    ]
    db_session.add_all(targets)
    await db_session.flush()
    db_session.add_all(
        [
            PublisherBlacklist(
                publisher_id=publisher["user"]["id"],
                blocked_user_id=target.id,
            )
            for target in targets[:500]
        ]
    )
    await db_session.commit()

    resp = await _add_blacklist(client, publisher, targets[500].id)
    assert resp.status_code == 400
    assert resp.json()["detail"] == LIMIT_EXCEEDED_DETAIL

    # 已存在的重复拉黑在上限处仍幂等成功（查重先于计数）
    resp = await _add_blacklist(client, publisher, targets[0].id)
    assert resp.status_code == 200


async def test_blacklist_requires_publish_permission(
    client: AsyncClient, db_session, make_captcha
):
    user = await _register(client, make_captcha, "nopub@example.com", "NoPub")
    await _set_can_publish(db_session, user["user"]["id"], False)

    headers = auth_header(user)
    mine = await client.get("/blacklist/mine", headers=headers)
    assert mine.status_code == 403
    assert mine.json()["detail"] == PUBLISHER_ONLY_DETAIL
    assert (
        await client.get("/blacklist/lookup", params={"q": "NoPub"}, headers=headers)
    ).status_code == 403
    assert (
        await _add_blacklist(client, user, user["user"]["id"])
    ).status_code == 403
    assert (await client.delete("/blacklist/999", headers=headers)).status_code == 403


# ---------------------------------------------------------------------------
# lookup：完全匹配 / 无列表 / 限流
# ---------------------------------------------------------------------------


async def test_lookup_only_exact_match(client: AsyncClient, make_captcha):
    publisher = await _register(client, make_captcha, "look.pub@example.com", "LookPub")
    target = await _register(client, make_captcha, "look.target@example.com", "LookTarget")
    headers = auth_header(publisher)
    target_id = target["user"]["id"]

    by_username = await client.get(
        "/blacklist/lookup", params={"q": "LookTarget"}, headers=headers
    )
    assert by_username.status_code == 200
    data = by_username.json()
    assert set(data) == {"id", "username", "avatar_url"}
    assert data["id"] == target_id
    assert "email" not in data

    by_email = await client.get(
        "/blacklist/lookup", params={"q": "look.target@example.com"}, headers=headers
    )
    assert by_email.status_code == 200
    assert by_email.json()["id"] == target_id

    # 前缀/模糊不命中，绝不返回候选列表
    prefix = await client.get(
        "/blacklist/lookup", params={"q": "LookTar"}, headers=headers
    )
    assert prefix.status_code == 404
    assert prefix.json()["detail"] == TARGET_NOT_FOUND_DETAIL

    unknown = await client.get(
        "/blacklist/lookup", params={"q": "NoSuchUser"}, headers=headers
    )
    assert unknown.status_code == 404

    # q 为空 / 过短 422
    assert (
        await client.get("/blacklist/lookup", params={"q": ""}, headers=headers)
    ).status_code == 422
    assert (
        await client.get("/blacklist/lookup", params={"q": "x"}, headers=headers)
    ).status_code == 422
    assert (
        await client.get("/blacklist/lookup", params={"q": "   "}, headers=headers)
    ).status_code == 422


async def test_lookup_rate_limited_per_user(client: AsyncClient, make_captcha):
    publisher = await _register(client, make_captcha, "rate.pub@example.com", "RatePub")
    await _register(client, make_captcha, "rate.target@example.com", "RateTarget")
    headers = auth_header(publisher)

    statuses = []
    for _ in range(11):
        resp = await client.get(
            "/blacklist/lookup", params={"q": "RateTarget"}, headers=headers
        )
        statuses.append(resp.status_code)
    assert statuses[:10] == [200] * 10
    assert statuses[10] == 429

    limited = await client.get(
        "/blacklist/lookup", params={"q": "RateTarget"}, headers=headers
    )
    assert limited.json()["detail"]["code"] == "blacklist_lookup_rate_limited"


# ---------------------------------------------------------------------------
# 接单校验
# ---------------------------------------------------------------------------


async def test_blacklisted_user_accept_returns_403(
    client: AsyncClient, admin_user: dict, make_captcha
):
    publisher_a = await _register(client, make_captcha, "acc.a@example.com", "AccA")
    publisher_c = await _register(client, make_captcha, "acc.c@example.com", "AccC")
    booster = await _register(client, make_captcha, "acc.b@example.com", "AccB")
    await _fund(client, admin_user, publisher_a, "200.00")
    await _fund(client, admin_user, publisher_c, "200.00")

    order_a = await _create_order(client, publisher_a)
    order_c = await _create_order(client, publisher_c)

    resp = await _add_blacklist(client, publisher_a, booster["user"]["id"])
    assert resp.status_code == 200

    blocked = await _accept(client, order_a["id"], booster)
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == ACCEPT_BLOCKED_DETAIL

    # 命中黑名单不产生 claim
    result = await client.get(
        f"/orders/{order_a['id']}/claims", headers=auth_header(publisher_a)
    )
    assert result.status_code == 200
    assert result.json()["total"] == 0

    # 其他发单人不受影响
    allowed = await _accept(client, order_c["id"], booster)
    assert allowed.status_code == 200, allowed.text

    # 解除后立即恢复
    resp = await client.delete(
        f"/blacklist/{booster['user']['id']}", headers=auth_header(publisher_a)
    )
    assert resp.status_code == 204
    restored = await _accept(client, order_a["id"], booster)
    assert restored.status_code == 200, restored.text


async def test_unblocked_user_can_accept(client: AsyncClient, admin_user: dict, make_captcha):
    publisher = await _register(client, make_captcha, "free.pub@example.com", "FreePub")
    booster = await _register(client, make_captcha, "free.b@example.com", "FreeB")
    await _fund(client, admin_user, publisher, "200.00")
    order = await _create_order(client, publisher)

    resp = await _accept(client, order["id"], booster)
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# 列表/详情标记（等待字段不下发）
# ---------------------------------------------------------------------------


async def _enable_deposit_with_tiers(db_session) -> None:
    """开启保证金：0 元档等待 30 秒，1000 元档等待 5 秒。"""
    db_session.add(
        DepositSetting(
            id=1,
            enabled=True,
            return_cooldown_days=7,
            default_compensation=Decimal("20.00"),
            settlement_mode="AFTER_DELIVERY",
            global_booster_quota=5,
        )
    )
    db_session.add_all(
        [
            DepositTier(
                threshold=Decimal("0.00"),
                wait_seconds=30,
                exempt_compensation=False,
                settle_hours=None,
                enabled=True,
            ),
            DepositTier(
                threshold=Decimal("1000.00"),
                wait_seconds=5,
                exempt_compensation=True,
                settle_hours=None,
                enabled=True,
            ),
        ]
    )
    await db_session.commit()


async def test_list_and_detail_mark_accept_blocked(
    client: AsyncClient, admin_user: dict, db_session, make_captcha
):
    publisher_a = await _register(client, make_captcha, "mark.a@example.com", "MarkA")
    publisher_d = await _register(client, make_captcha, "mark.d@example.com", "MarkD")
    booster = await _register(client, make_captcha, "mark.b@example.com", "MarkB")
    await _fund(client, admin_user, publisher_a, "200.00")
    await _fund(client, admin_user, publisher_d, "200.00")

    await _enable_deposit_with_tiers(db_session)
    # 查看者 0 保证金 → 等待 30 秒；钱包行缺失时也按 0 余额
    db_session.add(Wallet(user_id=booster["user"]["id"], deposit_balance=Decimal("0.00")))
    await db_session.commit()

    order_a = await _create_order(client, publisher_a)
    order_d = await _create_order(client, publisher_d)
    assert (await _add_blacklist(client, publisher_a, booster["user"]["id"])).status_code == 200

    headers = auth_header(booster)
    hall = await client.get("/orders/", params={"page_size": 50}, headers=headers)
    assert hall.status_code == 200, hall.text
    items = {item["id"]: item for item in hall.json()["items"]}
    # 被拉黑用户仍能看到该发单人的订单（只限制接单，不限制查看）
    assert order_a["id"] in items

    blocked_item = items[order_a["id"]]
    assert blocked_item["accept_blocked"] is True
    assert blocked_item["accept_blocked_reason"] == ACCEPT_BLOCKED_DETAIL
    assert blocked_item["accept_wait_seconds"] is None
    assert blocked_item["accept_available_at"] is None
    assert blocked_item["accept_window_pending"] is False

    other_item = items[order_d["id"]]
    assert other_item["accept_blocked"] is False
    assert other_item["accept_blocked_reason"] is None
    assert other_item["accept_wait_seconds"] == 30
    assert other_item["accept_available_at"] is not None

    detail = await client.get(f"/orders/{order_a['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    detail_body = detail.json()
    assert detail_body["accept_blocked"] is True
    assert detail_body["accept_blocked_reason"] == ACCEPT_BLOCKED_DETAIL
    assert detail_body["accept_wait_seconds"] is None
    assert detail_body["accept_available_at"] is None

    detail_other = await client.get(f"/orders/{order_d['id']}", headers=headers)
    assert detail_other.json()["accept_blocked"] is False
    assert detail_other.json()["accept_wait_seconds"] == 30


# ---------------------------------------------------------------------------
# 新单通知排除被拉黑用户
# ---------------------------------------------------------------------------


async def test_new_order_notifications_exclude_blacklisted(
    client: AsyncClient, db_session, make_captcha
):
    publisher = await _register(client, make_captcha, "notif.pub@example.com", "NotifPub")
    blocked = await _register(client, make_captcha, "notif.blocked@example.com", "NotifBlocked")
    allowed = await _register(client, make_captcha, "notif.ok@example.com", "NotifOk")

    db_session.add(
        PublisherBlacklist(
            publisher_id=publisher["user"]["id"],
            blocked_user_id=blocked["user"]["id"],
        )
    )
    order = Order(
        user_id=publisher["user"]["id"],
        game_name="王者荣耀",
        current_rank="钻石",
        target_rank="王者",
        price=Decimal("100.00"),
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(order)
    await db_session.commit()

    count = await notify_boosters_new_order(
        db_session, order=order, exclude_user_id=publisher["user"]["id"]
    )
    assert count == 1  # 只有 allowed 收通知（发布人自己排除，blocked 被黑名单排除）

    notifications = (
        await db_session.execute(
            select(Notification.user_id).where(Notification.ref_id == order.id)
        )
    ).scalars().all()
    assert set(notifications) == {allowed["user"]["id"]}
    assert blocked["user"]["id"] not in notifications


async def test_blacklisted_user_still_visible_in_hall(
    client: AsyncClient, admin_user: dict, make_captcha
):
    """契约明确：不改 build_hall_visibility_scope，被拉黑用户仍能看到订单。"""
    publisher = await _register(client, make_captcha, "hall.pub@example.com", "HallPub")
    booster = await _register(client, make_captcha, "hall.b@example.com", "HallB")
    await _fund(client, admin_user, publisher, "200.00")
    order = await _create_order(client, publisher)
    await _add_blacklist(client, publisher, booster["user"]["id"])

    hall = await client.get(
        "/orders/", params={"page_size": 50}, headers=auth_header(booster)
    )
    assert order["id"] in {item["id"] for item in hall.json()["items"]}
