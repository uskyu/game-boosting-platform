"""指定派单（designated booster）核心行为测试。

覆盖：
- 创建校验：目标不存在 / 自己 / 管理员 / 停用 / 不可接单 / 被拉黑 / 多名额；
- 大厅列表、hall-digest、详情在目标、第三人、发布人、管理员四种视角的可见性；
- 第三人接单 403、目标接单成功；accept-captcha 对非指定人 403；
- 指定单的 max_claims 保护、管理员改派同步指定人；
- 匿名公开搜索不返回指定单且 boss_contact 置空；
- 聊天第三人无法对指定单发起咨询；
- 响应里的 designated_booster brief 不含 email。
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from app.models.order import Order
from app.models.publisher_blacklist import PublisherBlacklist
from app.models.site_setting import SiteSetting
from app.models.user import User
from tests.conftest import auth_header

ACCEPT_OTHER = "该订单已指定给其他用户，无法接单"
CONSULT_OTHER = "该订单已指定给其他用户，暂无法发起订单咨询"
MAX_CLAIMS_GUARD = "指定派单只能保留 1 个接单名额"
_SINGLE_SLOT_GUARD = "指定派单只能设置 1 个接单名额"
_PASSWORD = "Passw0rd123"


async def _register(
    client: AsyncClient, make_captcha, *, email: str, username: str
) -> dict:
    resp = await client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "password": _PASSWORD,
            **make_captcha(),
        },
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _fund(client: AsyncClient, admin_user: dict, user: dict, amount=500) -> None:
    resp = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": amount, "reason": "designated order test"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code in (200, 201), resp.text


async def _create_order(client: AsyncClient, publisher: dict, **overrides) -> dict:
    body = {
        "game_name": "王者荣耀",
        "current_rank": "钻石",
        "target_rank": "王者",
        "price": "100.00",
        "title": "指定派单测试订单",
        "boss_contact": "BOSS-001",
        **overrides,
    }
    resp = await client.post(
        "/orders/create", json=body, headers=auth_header(publisher)
    )
    return {"status": resp.status_code, "body": resp.json(), "text": resp.text}


async def _create_designated(
    client: AsyncClient, publisher: dict, target_id: int, **overrides
) -> dict:
    result = await _create_order(
        client, publisher, designated_booster_id=target_id, **overrides
    )
    assert result["status"] == 201, result["text"]
    return result["body"]


async def _set_user(db_session, user_id: int, **values) -> None:
    await db_session.execute(update(User).where(User.id == user_id).values(**values))
    await db_session.commit()


async def _order_row(db_session, order_id: int) -> Order:
    return (
        await db_session.execute(select(Order).where(Order.id == order_id))
    ).scalar_one()


async def _list_ids(client: AsyncClient, user: dict) -> list[int]:
    resp = await client.get("/orders/", headers=auth_header(user), params={"page_size": 100})
    assert resp.status_code == 200, resp.text
    return [item["id"] for item in resp.json()["items"]]


async def _digest(client: AsyncClient, user: dict) -> dict:
    resp = await client.get("/orders/hall-digest", headers=auth_header(user))
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 创建校验
# ---------------------------------------------------------------------------


async def test_create_designated_rejects_invalid_targets(
    client: AsyncClient,
    db_session,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
    make_captcha,
):
    publisher = registered_user
    publisher_id = publisher["user"]["id"]
    target_id = booster_user["user"]["id"]
    admin_id = admin_user["user"]["id"]
    await _fund(client, admin_user, publisher)

    # 1) 目标不存在
    result = await _create_order(client, publisher, designated_booster_id=999999)
    assert result["status"] == 400, result["text"]
    assert result["body"]["detail"] == "指定用户不存在"

    # 2) 不能指定自己
    result = await _create_order(client, publisher, designated_booster_id=publisher_id)
    assert result["status"] == 400, result["text"]
    assert result["body"]["detail"] == "不能指定自己"

    # 3) 不能指定管理员
    result = await _create_order(client, publisher, designated_booster_id=admin_id)
    assert result["status"] == 400, result["text"]
    assert result["body"]["detail"] == "不能指定管理员"

    # 4) 目标被停用
    await _set_user(db_session, target_id, is_active=False)
    result = await _create_order(client, publisher, designated_booster_id=target_id)
    assert result["status"] == 400, result["text"]
    assert result["body"]["detail"] == "该用户已被停用"
    await _set_user(db_session, target_id, is_active=True)

    # 5) 目标当前不可接单
    await _set_user(db_session, target_id, can_accept=False)
    result = await _create_order(client, publisher, designated_booster_id=target_id)
    assert result["status"] == 400, result["text"]
    assert result["body"]["detail"] == "该用户当前不可接单"
    await _set_user(db_session, target_id, can_accept=True)

    # 6) 目标被发布人拉黑
    db_session.add(
        PublisherBlacklist(
            publisher_id=publisher_id,
            blocked_user_id=target_id,
            reason="designated test",
        )
    )
    await db_session.commit()
    result = await _create_order(client, publisher, designated_booster_id=target_id)
    assert result["status"] == 400, result["text"]
    assert result["body"]["detail"] == "该用户在你的黑名单中"


async def test_create_designated_rejects_multiple_claims(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
):
    publisher = registered_user
    await _fund(client, admin_user, publisher)
    result = await _create_order(
        client,
        publisher,
        designated_booster_id=booster_user["user"]["id"],
        max_claims=2,
    )
    assert result["status"] == 422, result["text"]
    assert _SINGLE_SLOT_GUARD in result["text"]


async def test_create_designated_success_and_brief_has_no_email(
    client: AsyncClient,
    db_session,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
):
    publisher = registered_user
    target_id = booster_user["user"]["id"]
    await _fund(client, admin_user, publisher)

    body = await _create_designated(client, publisher, target_id)
    assert body["designated_booster_id"] == target_id
    assert body["max_claims"] == 1
    brief = body["designated_booster"]
    assert brief["id"] == target_id
    assert set(brief) == {"id", "username", "avatar_url"}
    assert "email" not in brief

    row = await _order_row(db_session, body["id"])
    assert row.designated_booster_id == target_id
    # 托管仍按 price × 1（指定单只有 1 个名额）
    assert body["escrow_amount"] == "100.00"


# ---------------------------------------------------------------------------
# 四种视角：大厅列表 / hall-digest / 详情
# ---------------------------------------------------------------------------


async def test_designated_visibility_for_target_third_party_publisher_admin(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
    make_captcha,
):
    publisher = registered_user
    target = booster_user
    third = await _register(
        client,
        make_captcha,
        email="designated-third@example.com",
        username="DesignatedThird",
    )
    target_id = target["user"]["id"]
    await _fund(client, admin_user, publisher)
    order = await _create_designated(client, publisher, target_id)

    # 大厅列表：只有指定人能看到这单（2026-11-22 起大厅为 claimable-only，
    # 发布人/管理员的大厅不再因为「自己的单 / 不过滤」而列出指定单）
    assert order["id"] in await _list_ids(client, target)
    assert order["id"] not in await _list_ids(client, third)
    assert order["id"] not in await _list_ids(client, publisher)
    assert order["id"] not in await _list_ids(client, admin_user)

    # 大厅摘要（含顶部统计：只有目标能把这单算进自己的「待接单」）
    target_digest = await _digest(client, target)
    third_digest = await _digest(client, third)
    publisher_digest = await _digest(client, publisher)
    admin_digest = await _digest(client, admin_user)
    assert target_digest["count"] == 1
    assert target_digest["pending_count"] == 1
    assert third_digest["count"] == 0
    assert third_digest["pending_count"] == 0
    # 指定单对发布人/管理员同样不可抢 → 不进他们的大厅口径
    assert publisher_digest["count"] == 0
    assert admin_digest["count"] == 0

    # 详情
    detail = f"/orders/{order['id']}"
    target_resp = await client.get(detail, headers=auth_header(target))
    assert target_resp.status_code == 200, target_resp.text
    assert target_resp.json()["designated_booster_id"] == target_id
    assert "email" not in target_resp.json()["designated_booster"]

    third_resp = await client.get(detail, headers=auth_header(third))
    assert third_resp.status_code == 403, third_resp.text
    assert third_resp.json()["detail"] == "无权访问此订单"

    publisher_resp = await client.get(detail, headers=auth_header(publisher))
    assert publisher_resp.status_code == 200, publisher_resp.text
    admin_resp = await client.get(detail, headers=auth_header(admin_user))
    assert admin_resp.status_code == 200, admin_resp.text


# ---------------------------------------------------------------------------
# 接单与验证码
# ---------------------------------------------------------------------------


async def test_only_designated_user_can_accept(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
    make_captcha,
):
    publisher = registered_user
    target = booster_user
    third = await _register(
        client,
        make_captcha,
        email="designated-accept-third@example.com",
        username="DesignatedAcceptThird",
    )
    await _fund(client, admin_user, publisher)
    order = await _create_designated(client, publisher, target["user"]["id"])

    resp = await client.put(
        f"/orders/{order['id']}/accept", headers=auth_header(third)
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == ACCEPT_OTHER

    resp = await client.put(
        f"/orders/{order['id']}/accept", headers=auth_header(target)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["booster_id"] == target["user"]["id"]
    assert body["designated_booster_id"] == target["user"]["id"]
    assert body["status"] == "LOCKED"


async def test_accept_captcha_rejects_non_designated_user(
    client: AsyncClient,
    db_session,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
    make_captcha,
):
    publisher = registered_user
    target = booster_user
    third = await _register(
        client,
        make_captcha,
        email="designated-captcha-third@example.com",
        username="DesignatedCaptchaThird",
    )
    await _fund(client, admin_user, publisher)
    order = await _create_designated(client, publisher, target["user"]["id"])

    await db_session.execute(
        update(SiteSetting)
        .where(SiteSetting.id == 1)
        .values(accept_captcha_enabled=True)
    )
    await db_session.commit()

    resp = await client.get(
        "/orders/accept-captcha",
        params={"order_id": order["id"]},
        headers=auth_header(third),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == ACCEPT_OTHER

    # 指定人可以正常拿到绑定到该订单的验证码
    resp = await client.get(
        "/orders/accept-captcha",
        params={"order_id": order["id"]},
        headers=auth_header(target),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["captcha_id"]


# ---------------------------------------------------------------------------
# 编辑保护与管理员改派
# ---------------------------------------------------------------------------


async def test_update_designated_order_cannot_change_max_claims(
    client: AsyncClient,
    db_session,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
):
    publisher = registered_user
    target_id = booster_user["user"]["id"]
    await _fund(client, admin_user, publisher)
    order = await _create_designated(client, publisher, target_id)
    url = f"/orders/{order['id']}"

    resp = await client.put(
        url, json={"max_claims": 2}, headers=auth_header(publisher)
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == MAX_CLAIMS_GUARD

    # 显式 null 同样不允许（下游 setattr 会写成 NULL）
    resp = await client.put(
        url, json={"max_claims": None}, headers=auth_header(publisher)
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == MAX_CLAIMS_GUARD

    # 显式 1 保持允许
    resp = await client.put(
        url, json={"max_claims": 1}, headers=auth_header(publisher)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["max_claims"] == 1
    assert resp.json()["designated_booster_id"] == target_id


async def test_db_check_constraint_rejects_designated_multi_slot(
    client: AsyncClient,
    db_session,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
):
    """数据库表级 CHECK 兜底：绕过 API 直接把指定单改成多名额也必须失败。"""
    publisher = registered_user
    await _fund(client, admin_user, publisher)
    order = await _create_designated(client, publisher, booster_user["user"]["id"])

    # MySQL 对 CHECK 违约抛 OperationalError(3819)，SQLAlchemy 统一包装为
    # DBAPIError；用基类兼容不同驱动/版本。
    with pytest.raises(DBAPIError):
        await db_session.execute(
            update(Order)
            .where(Order.id == order["id"])
            .values(max_claims=2)
        )
        await db_session.flush()
    await db_session.rollback()


async def test_admin_assign_overrides_designated_booster(
    client: AsyncClient,
    db_session,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
    make_captcha,
):
    publisher = registered_user
    designated = booster_user
    replacement = await _register(
        client,
        make_captcha,
        email="designated-replacement@example.com",
        username="DesignatedReplacement",
    )
    replacement_id = replacement["user"]["id"]
    await _fund(client, admin_user, publisher)
    order = await _create_designated(client, publisher, designated["user"]["id"])

    resp = await client.put(
        f"/admin/orders/{order['id']}/assign",
        json={"booster_id": replacement_id, "reason": "管理员改派"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["booster_id"] == replacement_id
    assert body["designated_booster_id"] == replacement_id
    assert body["status"] == "LOCKED"

    row = await _order_row(db_session, order["id"])
    assert row.booster_id == replacement_id
    assert row.designated_booster_id == replacement_id


# ---------------------------------------------------------------------------
# 公开搜索与聊天
# ---------------------------------------------------------------------------


async def test_public_search_hides_designated_order_and_boss_contact(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
):
    publisher = registered_user
    target_id = booster_user["user"]["id"]
    await _fund(client, admin_user, publisher)

    public_order = (
        await _create_order(
            client, publisher, title="公开搜索订单", boss_contact="PUBLIC-BOSS"
        )
    )["body"]
    designated_order = (
        await _create_order(
            client,
            publisher,
            title="指定搜索订单",
            boss_contact="SECRET-BOSS",
            designated_booster_id=target_id,
        )
    )["body"]

    resp = await client.get(
        "/search/", params={"q": "王者荣耀", "type": "orders", "page_size": 100}
    )
    assert resp.status_code == 200, resp.text
    orders = resp.json()["orders"]
    ids = [item["id"] for item in orders["items"]]
    assert designated_order["id"] not in ids
    assert public_order["id"] in ids
    assert orders["total"] == 1
    public_item = next(
        item for item in orders["items"] if item["id"] == public_order["id"]
    )
    # 匿名搜索不得泄漏老板联系 ID（既有公开泄漏，本次一并修复）
    assert public_item["boss_contact"] is None


async def test_chat_third_user_cannot_consult_designated_order(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
    make_captcha,
):
    publisher = registered_user
    target = booster_user
    third = await _register(
        client,
        make_captcha,
        email="designated-chat-third@example.com",
        username="DesignatedChatThird",
    )
    await _fund(client, admin_user, publisher)
    order = await _create_designated(client, publisher, target["user"]["id"])

    resp = await client.post(
        "/chat/conversations",
        json={
            "target_user_id": publisher["user"]["id"],
            "order_id": order["id"],
        },
        headers=auth_header(third),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == CONSULT_OTHER

    # 指定人仍可正常发起接单前咨询
    resp = await client.post(
        "/chat/conversations",
        json={
            "target_user_id": publisher["user"]["id"],
            "order_id": order["id"],
        },
        headers=auth_header(target),
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# 续单兜底：标题「指定<昵称>」但没带 designated_booster_id
#
# 2026-11-24 线上反馈：有些发单人点了续单，订单却没有变成指定单。
# 根因是续单自 2026-10-04（311596e）起就会把标题改成「指定<原打手昵称>」，
# 但指定派单字段到 2026-11-17（ddfa754）才有；跑着旧 chunk 的客户端
# （微信 XWEB 长驻页面）继续发单时，标题说指定、实际是公开单。
# 服务端因此按标题兜底：名字精确命中就打上指定人，命中但对方不能接就报错，
# 绝不静默变成公开单。
# ---------------------------------------------------------------------------


async def test_title_only_repeat_becomes_designated(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
):
    """旧客户端只把标题改成「指定<昵称>」时，服务端补上指定人。"""
    publisher = registered_user
    target = booster_user
    target_id = target["user"]["id"]
    target_name = target["user"]["username"]
    await _fund(client, admin_user, publisher)

    result = await _create_order(
        client, publisher, title=f"指定{target_name}", designated_booster_id=None
    )
    assert result["status"] == 201, result["text"]
    body = result["body"]
    assert body["designated_booster_id"] == target_id
    # 推断路径必须把名额锁成 1，否则触发 ck_orders_designated_single_slot
    assert body["max_claims"] == 1
    assert body["designated_booster"]["username"] == target_name


async def test_title_only_repeat_forces_single_slot(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
):
    """旧客户端可能把 max_claims 填成多人；推断成指定单后仍须落成 1 人。"""
    publisher = registered_user
    target_name = booster_user["user"]["username"]
    await _fund(client, admin_user, publisher)

    result = await _create_order(
        client, publisher, title=f"指定{target_name}", max_claims=3
    )
    assert result["status"] == 201, result["text"]
    assert result["body"]["max_claims"] == 1
    assert result["body"]["designated_booster_id"] == booster_user["user"]["id"]


async def test_title_without_matching_username_stays_public(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
):
    """标题里不是某个用户的完整昵称时保持公开单，不擅自指派。"""
    publisher = registered_user
    await _fund(client, admin_user, publisher)

    result = await _create_order(
        client, publisher, title="指定派单测试订单", designated_booster_id=None
    )
    assert result["status"] == 201, result["text"]
    assert result["body"]["designated_booster_id"] is None

    # 前缀匹配也不算命中：必须是完整昵称
    partial = booster_user["user"]["username"][:2]
    result = await _create_order(client, publisher, title=f"指定{partial}")
    assert result["status"] == 201, result["text"]
    assert result["body"]["designated_booster_id"] is None


async def test_title_only_repeat_rejects_unavailable_target(
    client: AsyncClient,
    db_session,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
):
    """标题命中一个当前不可接单的打手时明确报错，不能悄悄发成公开单。"""
    publisher = registered_user
    target_id = booster_user["user"]["id"]
    target_name = booster_user["user"]["username"]
    await _fund(client, admin_user, publisher)

    await _set_user(db_session, target_id, can_accept=False)
    result = await _create_order(client, publisher, title=f"指定{target_name}")
    assert result["status"] == 400, result["text"]
    assert result["body"]["detail"] == "标题指定的用户当前不可接单"
    await _set_user(db_session, target_id, can_accept=True)

    await _set_user(db_session, target_id, is_active=False)
    result = await _create_order(client, publisher, title=f"指定{target_name}")
    assert result["status"] == 400, result["text"]
    assert result["body"]["detail"] == "标题指定的用户已被停用"
