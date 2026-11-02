"""会话标题 / 对方用户（counterpart）的序列化契约。

三人订单会话（客户 + 打手 + 客服）下，前端标题曾把 ADMIN 也拼进去，
other_participants 为空时又退化成“订单 #id”。后端新增 counterpart：
按订单角色返回“当前视角的对方”，并保持 participants / other_participants
的既有语义不变。
"""

from httpx import AsyncClient

from app.models.chat import Conversation, ConversationParticipant, ConversationType
from tests.conftest import auth_header


async def _create_order(client: AsyncClient, publisher: dict) -> dict:
    response = await client.post(
        "/orders/create",
        json={
            "title": "测试订单",
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "100.00",
            "boss_contact": "B0001",
        },
        headers=auth_header(publisher),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _open_order_conversation(
    client: AsyncClient,
    user: dict,
    *,
    target_user_id: int,
    order_id: int,
) -> dict:
    response = await client.post(
        "/chat/conversations",
        headers=auth_header(user),
        json={"target_user_id": target_user_id, "order_id": order_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _get_conversation(
    client: AsyncClient,
    user: dict,
    conversation_id: int,
) -> dict:
    response = await client.get(
        f"/chat/conversations/{conversation_id}",
        headers=auth_header(user),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _other_participant_ids(payload: dict) -> set[int]:
    return {item["id"] for item in payload["other_participants"]}


async def test_order_conversation_counterpart_resolves_by_viewer(
    client: AsyncClient,
    registered_user: dict,
    booster_user: dict,
    admin_user: dict,
) -> None:
    publisher = registered_user
    booster = booster_user
    admin = admin_user

    top_up = await client.post(
        f"/admin/wallets/{publisher['user']['id']}/adjust",
        json={"amount": 500, "reason": "test chat counterpart"},
        headers=auth_header(admin),
    )
    assert top_up.status_code in (200, 201), top_up.text

    order = await _create_order(client, publisher)
    accepted = await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(booster),
    )
    assert accepted.status_code == 200, accepted.text

    conversation = await _open_order_conversation(
        client,
        publisher,
        target_user_id=booster["user"]["id"],
        order_id=order["id"],
    )
    conversation_id = conversation["id"]

    invited = await client.post(
        f"/chat/conversations/{conversation_id}/invite-admin",
        headers=auth_header(publisher),
    )
    assert invited.status_code == 200, invited.text

    publisher_id = publisher["user"]["id"]
    booster_id = booster["user"]["id"]
    admin_id = admin["user"]["id"]

    # 老板视角：对方是打手；客服在 other_participants 里，但不会成为 counterpart
    owner_view = await _get_conversation(client, publisher, conversation_id)
    assert owner_view["counterpart"]["id"] == booster_id
    assert owner_view["counterpart"]["username"] == booster["user"]["username"]
    assert publisher_id not in _other_participant_ids(owner_view)
    # 既有语义保持：完整的参与者列表仍包含客服
    assert {admin_id, booster_id} <= _other_participant_ids(owner_view)
    assert owner_view["order"]["user_username"] == publisher["user"]["username"]
    assert owner_view["order"]["booster_username"] == booster["user"]["username"]

    # 打手视角：对方是下单方
    booster_view = await _get_conversation(client, booster, conversation_id)
    assert booster_view["counterpart"]["id"] == publisher_id
    assert booster_view["counterpart"]["username"] == publisher["user"]["username"]
    assert booster_id not in _other_participant_ids(booster_view)

    # 管理员视角：优先下单方
    admin_view = await _get_conversation(client, admin, conversation_id)
    assert admin_view["counterpart"]["id"] == publisher_id
    assert admin_view["counterpart"]["username"] == publisher["user"]["username"]
    assert admin_id not in _other_participant_ids(admin_view)


async def test_solo_participant_conversation_does_not_500(
    client: AsyncClient,
    registered_user: dict,
    db_session,
) -> None:
    """历史数据里只有自己一个参与者的会话也必须正常返回。"""
    user_id = registered_user["user"]["id"]
    conversation = Conversation(type=ConversationType.PRIVATE)
    db_session.add(conversation)
    await db_session.flush()
    db_session.add(
        ConversationParticipant(
            conversation_id=conversation.id,
            user_id=user_id,
            role_snapshot=registered_user["user"]["role"],
        )
    )
    await db_session.commit()

    payload = await _get_conversation(client, registered_user, conversation.id)

    assert payload["counterpart"] is None
    assert payload["other_participants"] == []
    assert payload["participants"][0]["user_id"] == user_id


async def test_order_conversation_with_only_owner_participant_uses_order_booster(
    client: AsyncClient,
    registered_user: dict,
    booster_user: dict,
    admin_user: dict,
    db_session,
) -> None:
    """遗留会话只登记了老板时，counterpart 仍可从订单角色解析出打手。"""
    publisher = registered_user
    booster = booster_user

    top_up = await client.post(
        f"/admin/wallets/{publisher['user']['id']}/adjust",
        json={"amount": 500, "reason": "test chat counterpart legacy"},
        headers=auth_header(admin_user),
    )
    assert top_up.status_code in (200, 201), top_up.text

    order = await _create_order(client, publisher)
    accepted = await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(booster),
    )
    assert accepted.status_code == 200, accepted.text

    assert accepted.json()["booster_id"] == booster["user"]["id"]

    conversation = Conversation(
        type=ConversationType.ORDER,
        order_id=order["id"],
    )
    db_session.add(conversation)
    await db_session.flush()
    db_session.add(
        ConversationParticipant(
            conversation_id=conversation.id,
            user_id=publisher["user"]["id"],
            role_snapshot=publisher["user"]["role"],
        )
    )
    await db_session.commit()

    payload = await _get_conversation(client, publisher, conversation.id)

    assert payload["counterpart"]["id"] == booster["user"]["id"]
    assert payload["counterpart"]["username"] == booster["user"]["username"]
    assert payload["other_participants"] == []
