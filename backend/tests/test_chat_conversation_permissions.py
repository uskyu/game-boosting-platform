"""订单会话建联权限、多会话隔离与限流回归。

覆盖 P0/P1a 的核心验收：
1. 两名非发布人不能基于订单建联；
2. 发布人分别与不同打手拥有独立会话，双方不能读取对方私聊；
3. 同一对并发仍只有一条，不同对并发可以各建一条；
4. 老板发布 → A 预咨询 → B 接单后，老板与 B 正常聊天且 A 记录保留；
5. 管理员加入三人会话不破坏同 pair 查找；管理员只能主动联系发布人；
6. can_accept=false 账号可打开/续聊已有会话，但不能新建 ORDER 会话；
7. 建联宽限流与「仅新建 ORDER 会话」严格限流；历史会话不消耗严格额度；
8. 数据库唯一键是 (order_id, counterpart_user_id)，同订单不同对方可共存。
"""

import asyncio

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.chat import (
    Conversation,
    ConversationParticipant,
    ConversationType,
)
from app.models.user import User, UserRole
from tests.conftest import auth_header

BOOSTER_PASSWORD = "BoostPass123"


async def _register_booster(
    client: AsyncClient,
    db_session,
    make_captcha,
    *,
    email: str,
    username: str,
) -> dict:
    """注册并提升为打手，返回重新登录后的令牌载荷。"""
    resp = await client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "password": BOOSTER_PASSWORD,
            **make_captcha(),
        },
    )
    assert resp.status_code in (200, 201), resp.text

    user = (
        await db_session.execute(select(User).where(User.email == email))
    ).scalar_one()
    user.role = UserRole.BOOSTER
    user.booster_quota = 5
    await db_session.commit()

    login = await client.post(
        "/auth/login", json={"email": email, "password": BOOSTER_PASSWORD}
    )
    assert login.status_code == 200, login.text
    return login.json()


async def _top_up(
    client: AsyncClient,
    admin: dict,
    user: dict,
    amount: int = 500,
) -> None:
    resp = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": amount, "reason": "test chat permissions"},
        headers=auth_header(admin),
    )
    assert resp.status_code in (200, 201), resp.text


async def _create_order(client: AsyncClient, publisher: dict) -> dict:
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "100.00",
        },
        headers=auth_header(publisher),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _open_conversation(
    client: AsyncClient,
    user: dict,
    *,
    target_user_id: int,
    order_id: int | None,
):
    return await client.post(
        "/chat/conversations",
        headers=auth_header(user),
        json={"target_user_id": target_user_id, "order_id": order_id},
    )


async def _set_can_accept(db_session, user_id: int, value: bool) -> None:
    user = (
        await db_session.execute(select(User).where(User.id == user_id))
    ).scalar_one()
    user.can_accept = value
    await db_session.commit()


async def _conversation_count(db_session, order_id: int) -> int:
    return int(
        (
            await db_session.execute(
                select(func.count(Conversation.id)).where(
                    Conversation.order_id == order_id
                )
            )
        ).scalar()
        or 0
    )


async def test_two_non_publishers_cannot_create_order_conversation(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
    db_session,
    make_captcha,
) -> None:
    publisher = registered_user
    booster_a = booster_user
    await _top_up(client, admin_user, publisher)
    order = await _create_order(client, publisher)

    booster_b = await _register_booster(
        client,
        db_session,
        make_captcha,
        email="perm_booster_b@example.com",
        username="PermBoosterB",
    )

    # A → B：双方都不是订单发布人，一律 403。
    resp = await _open_conversation(
        client,
        booster_a,
        target_user_id=booster_b["user"]["id"],
        order_id=order["id"],
    )
    assert resp.status_code == 403, resp.text
    assert "发布人" in resp.json()["detail"]

    # 反向同样拒绝。
    reverse = await _open_conversation(
        client,
        booster_b,
        target_user_id=booster_a["user"]["id"],
        order_id=order["id"],
    )
    assert reverse.status_code == 403, reverse.text

    assert await _conversation_count(db_session, order["id"]) == 0


async def test_publisher_has_independent_conversations_with_boosters(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
    db_session,
    make_captcha,
) -> None:
    publisher = registered_user
    booster_a = booster_user
    await _top_up(client, admin_user, publisher)
    order = await _create_order(client, publisher)

    booster_b = await _register_booster(
        client,
        db_session,
        make_captcha,
        email="perm_booster_c@example.com",
        username="PermBoosterC",
    )

    first = await _open_conversation(
        client,
        publisher,
        target_user_id=booster_a["user"]["id"],
        order_id=order["id"],
    )
    second = await _open_conversation(
        client,
        publisher,
        target_user_id=booster_b["user"]["id"],
        order_id=order["id"],
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["id"] != second.json()["id"]
    assert first.json()["counterpart_user_id"] == booster_a["user"]["id"]
    assert second.json()["counterpart_user_id"] == booster_b["user"]["id"]

    # A 不能读取 B 的会话，B 也不能读取 A 的。
    a_reads_b = await client.get(
        f"/chat/conversations/{second.json()['id']}",
        headers=auth_header(booster_a),
    )
    assert a_reads_b.status_code == 403, a_reads_b.text
    b_reads_a = await client.get(
        f"/chat/conversations/{first.json()['id']}",
        headers=auth_header(booster_b),
    )
    assert b_reads_a.status_code == 403, b_reads_a.text

    # 会话列表也各自只包含自己的会话。
    a_list = await client.get("/chat/conversations", headers=auth_header(booster_a))
    b_list = await client.get("/chat/conversations", headers=auth_header(booster_b))
    a_ids = {item["id"] for item in a_list.json()["items"]}
    b_ids = {item["id"] for item in b_list.json()["items"]}
    assert first.json()["id"] in a_ids
    assert second.json()["id"] not in a_ids
    assert second.json()["id"] in b_ids
    assert first.json()["id"] not in b_ids

    assert await _conversation_count(db_session, order["id"]) == 2


async def test_concurrent_different_counterparts_create_two_conversations(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
    db_session,
    make_captcha,
) -> None:
    publisher = registered_user
    booster_a = booster_user
    await _top_up(client, admin_user, publisher)
    order = await _create_order(client, publisher)

    booster_b = await _register_booster(
        client,
        db_session,
        make_captcha,
        email="perm_booster_concurrent@example.com",
        username="PermBoosterConcurrent",
    )

    first, second = await asyncio.gather(
        _open_conversation(
            client,
            publisher,
            target_user_id=booster_a["user"]["id"],
            order_id=order["id"],
        ),
        _open_conversation(
            client,
            publisher,
            target_user_id=booster_b["user"]["id"],
            order_id=order["id"],
        ),
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["id"] != second.json()["id"]
    assert await _conversation_count(db_session, order["id"]) == 2


async def test_owner_preconsult_a_then_b_accepts_keeps_a_conversation(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
    db_session,
    make_captcha,
) -> None:
    """核心验收：A 预咨询 → B 接单 → 老板与 B 聊天，A 的记录保留。"""
    publisher = registered_user
    booster_a = booster_user
    await _top_up(client, admin_user, publisher)
    order = await _create_order(client, publisher)

    booster_b = await _register_booster(
        client,
        db_session,
        make_captcha,
        email="perm_booster_accept@example.com",
        username="PermBoosterAccept",
    )

    # PENDING 阶段 A 先与老板咨询，成为该订单会话的 counterpart。
    preconsult = await _open_conversation(
        client,
        booster_a,
        target_user_id=publisher["user"]["id"],
        order_id=order["id"],
    )
    assert preconsult.status_code == 200, preconsult.text
    a_conversation_id = preconsult.json()["id"]
    assert preconsult.json()["counterpart_user_id"] == booster_a["user"]["id"]

    # B 接单成为 order.booster_id；A 没有 Claim。
    accepted = await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(booster_b),
    )
    assert accepted.status_code == 200, accepted.text
    order_detail = (
        await client.get(
            f"/orders/{order['id']}", headers=auth_header(publisher)
        )
    ).json()
    assert order_detail["booster_id"] == booster_b["user"]["id"]

    # 老板与 B 建联：service 不能把 order.booster_id 误当成 A 会话的对方，
    # 也不能因同订单已有 A 会话而拒绝/复用。
    b_conversation = await _open_conversation(
        client,
        publisher,
        target_user_id=booster_b["user"]["id"],
        order_id=order["id"],
    )
    assert b_conversation.status_code == 200, b_conversation.text
    b_conversation_id = b_conversation.json()["id"]
    assert b_conversation_id != a_conversation_id
    assert b_conversation.json()["counterpart_user_id"] == booster_b["user"]["id"]

    # A 的会话保留，且老板视角的对方仍是 A（不是 order.booster_id=B）。
    a_view = await client.get(
        f"/chat/conversations/{a_conversation_id}",
        headers=auth_header(publisher),
    )
    assert a_view.status_code == 200, a_view.text
    assert a_view.json()["counterpart"]["id"] == booster_a["user"]["id"]
    assert a_view.json()["counterpart"]["username"] == booster_a["user"]["username"]
    assert a_view.json()["counterpart_user_id"] == booster_a["user"]["id"]

    # A 可以继续发送消息；B 和 A 互相看不到对方私聊。
    sent = await client.post(
        f"/chat/conversations/{a_conversation_id}/messages",
        json={"content": "之前的咨询记录还在"},
        headers=auth_header(booster_a),
    )
    assert sent.status_code == 201, sent.text

    b_reads_a = await client.get(
        f"/chat/conversations/{a_conversation_id}",
        headers=auth_header(booster_b),
    )
    assert b_reads_a.status_code == 403, b_reads_a.text
    a_reads_b = await client.get(
        f"/chat/conversations/{b_conversation_id}",
        headers=auth_header(booster_a),
    )
    assert a_reads_b.status_code == 403, a_reads_b.text

    b_view = await client.get(
        f"/chat/conversations/{b_conversation_id}",
        headers=auth_header(booster_b),
    )
    assert b_view.status_code == 200, b_view.text
    assert b_view.json()["counterpart"]["id"] == publisher["user"]["id"]

    assert await _conversation_count(db_session, order["id"]) == 2


async def test_admin_invited_trio_does_not_break_pair_lookup(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
) -> None:
    publisher = registered_user
    booster = booster_user
    await _top_up(client, admin_user, publisher)
    order = await _create_order(client, publisher)

    created = await _open_conversation(
        client,
        publisher,
        target_user_id=booster["user"]["id"],
        order_id=order["id"],
    )
    assert created.status_code == 200, created.text
    conversation_id = created.json()["id"]

    invited = await client.post(
        f"/chat/conversations/{conversation_id}/invite-admin",
        headers=auth_header(publisher),
    )
    assert invited.status_code == 200, invited.text

    # 管理员成为第三个参与者后，发布人 + 打手的 pair 仍应命中同一会话。
    reopened = await _open_conversation(
        client,
        publisher,
        target_user_id=booster["user"]["id"],
        order_id=order["id"],
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["id"] == conversation_id

    participant_ids = {
        participant["user_id"] for participant in reopened.json()["participants"]
    }
    assert participant_ids == {
        publisher["user"]["id"],
        booster["user"]["id"],
        admin_user["user"]["id"],
    }


async def test_admin_can_contact_publisher_but_not_service_provider(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
) -> None:
    publisher = registered_user
    booster = booster_user
    await _top_up(client, admin_user, publisher)
    order = await _create_order(client, publisher)

    # 发布人主动与管理员建立新的订单会话：管理员不能作为新会话对方。
    owner_to_admin = await _open_conversation(
        client,
        publisher,
        target_user_id=admin_user["user"]["id"],
        order_id=order["id"],
    )
    assert owner_to_admin.status_code == 403, owner_to_admin.text

    # 管理员主动联系发布人：保留客服显式例外。
    admin_to_owner = await _open_conversation(
        client,
        admin_user,
        target_user_id=publisher["user"]["id"],
        order_id=order["id"],
    )
    assert admin_to_owner.status_code == 200, admin_to_owner.text
    assert admin_to_owner.json()["counterpart_user_id"] == admin_user["user"]["id"]
    # 管理员视角的对方是发布人。
    assert admin_to_owner.json()["counterpart"]["id"] == publisher["user"]["id"]

    # 管理员主动联系打手：不允许新建（同一订单下尚无 admin+booster 会话）。
    admin_to_booster = await _open_conversation(
        client,
        admin_user,
        target_user_id=booster["user"]["id"],
        order_id=order["id"],
    )
    assert admin_to_booster.status_code == 403, admin_to_booster.text

    # 发布人自己打开与客服的会话仍正常。
    owner_view = await client.get(
        f"/chat/conversations/{admin_to_owner.json()['id']}",
        headers=auth_header(publisher),
    )
    assert owner_view.status_code == 200, owner_view.text
    assert owner_view.json()["counterpart"]["id"] == admin_user["user"]["id"]


async def test_can_accept_false_can_reopen_existing_but_not_create_new(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
    db_session,
) -> None:
    publisher = registered_user
    booster = booster_user
    await _top_up(client, admin_user, publisher)

    first_order = await _create_order(client, publisher)
    created = await _open_conversation(
        client,
        booster,
        target_user_id=publisher["user"]["id"],
        order_id=first_order["id"],
    )
    assert created.status_code == 200, created.text
    conversation_id = created.json()["id"]

    await _set_can_accept(db_session, booster["user"]["id"], False)

    # 历史会话仍可打开/续聊，不受 can_accept=false 影响。
    reopened = await _open_conversation(
        client,
        booster,
        target_user_id=publisher["user"]["id"],
        order_id=first_order["id"],
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["id"] == conversation_id

    sent = await client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "售后还能继续聊"},
        headers=auth_header(booster),
    )
    assert sent.status_code == 201, sent.text

    # 新订单上，can_accept=false 的打手不能新建 ORDER 会话（两个方向都拒绝）。
    second_order = await _create_order(client, publisher)
    booster_creates = await _open_conversation(
        client,
        booster,
        target_user_id=publisher["user"]["id"],
        order_id=second_order["id"],
    )
    assert booster_creates.status_code == 403, booster_creates.text
    assert "不可接单" in booster_creates.json()["detail"]

    owner_creates = await _open_conversation(
        client,
        publisher,
        target_user_id=booster["user"]["id"],
        order_id=second_order["id"],
    )
    assert owner_creates.status_code == 403, owner_creates.text

    assert await _conversation_count(db_session, second_order["id"]) == 0


async def test_conversation_create_endpoint_has_broad_rate_limit(
    client: AsyncClient,
    registered_user: dict,
    admin_user: dict,
    rate_limits_enabled,
    monkeypatch,
) -> None:
    from app.api.endpoints import chat as chat_endpoint

    monkeypatch.setattr(chat_endpoint, "_CHAT_CONVERSATION_CREATE_RATE", (1, 60))

    first = await _open_conversation(
        client,
        registered_user,
        target_user_id=admin_user["user"]["id"],
        order_id=None,
    )
    assert first.status_code == 200, first.text

    second = await _open_conversation(
        client,
        registered_user,
        target_user_id=admin_user["user"]["id"],
        order_id=None,
    )
    assert second.status_code == 429, second.text
    detail = second.json()["detail"]
    assert detail["code"] == "chat_rate_limited"
    assert detail["retry_after"] >= 1


async def test_strict_new_order_rate_limit_skips_existing_conversation(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
    db_session,
    rate_limits_enabled,
    monkeypatch,
) -> None:
    from app.services import chat_service as chat_service_module

    monkeypatch.setattr(
        chat_service_module,
        "_NEW_ORDER_CONVERSATION_RATE",
        (1, 600),
    )

    publisher = registered_user
    booster = booster_user
    await _top_up(client, admin_user, publisher)

    first_order = await _create_order(client, publisher)
    created = await _open_conversation(
        client,
        publisher,
        target_user_id=booster["user"]["id"],
        order_id=first_order["id"],
    )
    assert created.status_code == 200, created.text

    # 打开/复用已有会话在严格桶之后之前就返回，不消耗额度。
    reopened = await _open_conversation(
        client,
        publisher,
        target_user_id=booster["user"]["id"],
        order_id=first_order["id"],
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["id"] == created.json()["id"]

    # 真正要新建第二个 ORDER 会话时才触发严格限流。
    second_order = await _create_order(client, publisher)
    limited = await _open_conversation(
        client,
        publisher,
        target_user_id=booster["user"]["id"],
        order_id=second_order["id"],
    )
    assert limited.status_code == 429, limited.text
    assert limited.json()["detail"]["code"] == "chat_rate_limited"
    assert await _conversation_count(db_session, second_order["id"]) == 0


async def test_order_counterpart_unique_constraint_allows_different_pairs(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
    db_session,
) -> None:
    """SQL 层校验：同订单同对方唯一、同订单不同对方可共存。"""
    publisher = registered_user
    booster_a = booster_user
    await _top_up(client, admin_user, publisher)
    order = await _create_order(client, publisher)

    created = await _open_conversation(
        client,
        publisher,
        target_user_id=booster_a["user"]["id"],
        order_id=order["id"],
    )
    assert created.status_code == 200, created.text

    # 模型只保留新的复合唯一键。
    unique_names = {
        constraint.name
        for constraint in Conversation.__table__.constraints
    }
    assert "uq_conversations_order_counterpart" in unique_names
    assert "uq_conversations_order_id" not in unique_names

    # 同一 (order_id, counterpart) 直接插入会撞新唯一键。
    duplicate = Conversation(
        type=ConversationType.ORDER,
        order_id=order["id"],
        counterpart_user_id=booster_a["user"]["id"],
    )
    db_session.add(duplicate)
    try:
        await db_session.flush()
    except IntegrityError as exc:
        await db_session.rollback()
        assert "uq_conversations_order_counterpart" in str(exc.orig)
    else:
        raise AssertionError("同订单同对方会话未被唯一键拦截")

    # 同订单不同对方可以插入，证明旧的 order_id 单列唯一键已移除。
    other_counterpart = Conversation(
        type=ConversationType.ORDER,
        order_id=order["id"],
        counterpart_user_id=admin_user["user"]["id"],
    )
    db_session.add(other_counterpart)
    await db_session.flush()
    await db_session.rollback()
