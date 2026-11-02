"""系统消息不应占用聊天未读/提醒。"""

from httpx import AsyncClient

from tests.conftest import auth_header


async def _create_order(client: AsyncClient, publisher: dict) -> dict:
    response = await client.post(
        "/orders/create",
        json={
            "title": "系统消息未读测试",
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


async def test_system_messages_do_not_count_as_unread(
    client: AsyncClient,
    registered_user: dict,
    booster_user: dict,
    admin_user: dict,
) -> None:
    publisher = registered_user
    booster = booster_user

    top_up = await client.post(
        f"/admin/wallets/{publisher['user']['id']}/adjust",
        json={"amount": 500, "reason": "test system unread"},
        headers=auth_header(admin_user),
    )
    assert top_up.status_code in (200, 201), top_up.text

    order = await _create_order(client, publisher)
    accepted = await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(booster),
    )
    assert accepted.status_code == 200, accepted.text

    conversation = await client.post(
        "/chat/conversations",
        headers=auth_header(publisher),
        json={"target_user_id": booster["user"]["id"], "order_id": order["id"]},
    )
    assert conversation.status_code == 200, conversation.text
    conversation_id = conversation.json()["id"]

    # 创建订单会话会自动写入订单卡片系统消息：双方都不应出现未读。
    for user in (publisher, booster):
        detail = await client.get(
            f"/chat/conversations/{conversation_id}",
            headers=auth_header(user),
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["unread_count"] == 0

        summary = await client.get(
            "/chat/unread-summary",
            headers=auth_header(user),
        )
        assert summary.status_code == 200, summary.text
        assert summary.json()["total_unread"] == 0

    # 真实用户消息仍然正常计未读。
    sent = await client.post(
        f"/chat/conversations/{conversation_id}/messages",
        headers=auth_header(publisher),
        json={"content": "真实消息"},
    )
    assert sent.status_code == 201, sent.text

    booster_detail = await client.get(
        f"/chat/conversations/{conversation_id}",
        headers=auth_header(booster),
    )
    assert booster_detail.json()["unread_count"] == 1

    booster_summary = await client.get(
        "/chat/unread-summary",
        headers=auth_header(booster),
    )
    assert booster_summary.json()["total_unread"] == 1
