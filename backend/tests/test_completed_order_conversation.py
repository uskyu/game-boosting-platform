"""Completed orders must still open an existing conversation.

The backend used to reject every order-based conversation request on a
terminal order (COMPLETED / CANCELLED) *before* looking up an existing
conversation, so the order page could never reopen a chat that both sides
had used while the order was active. Only *creating* a new conversation on
a terminal order should be refused.
"""

from httpx import AsyncClient
from sqlalchemy import select

from app.models.order import Order, OrderStatus
from tests.conftest import auth_header


async def _create_order(client: AsyncClient, publisher: dict, price: str = "100.00") -> dict:
    response = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": price,
        },
        headers=auth_header(publisher),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _complete_order(
    client: AsyncClient,
    publisher: dict,
    booster: dict,
    order_id: int,
) -> None:
    """Drive the order to COMPLETED: accept → deliver → approve."""
    accepted = await client.put(
        f"/orders/{order_id}/accept", headers=auth_header(booster)
    )
    assert accepted.status_code == 200, accepted.text

    delivered = await client.put(
        f"/orders/{order_id}/deliver", headers=auth_header(booster)
    )
    assert delivered.status_code == 200, delivered.text

    claims = await client.get(
        f"/orders/{order_id}/claims", headers=auth_header(publisher)
    )
    assert claims.status_code == 200, claims.text
    claim_id = claims.json()["items"][0]["id"]

    reviewed = await client.put(
        f"/orders/{order_id}/claims/{claim_id}/review",
        json={"action": "approve"},
        headers=auth_header(publisher),
    )
    assert reviewed.status_code == 200, reviewed.text


async def test_completed_order_reopens_existing_conversation(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
    db_session,
) -> None:
    """订单进行期聊过 → 完结后仍能打开同一个会话（回归：此前 403）。"""
    publisher = registered_user
    booster = booster_user

    top_up = await client.post(
        f"/admin/wallets/{publisher['user']['id']}/adjust",
        json={"amount": 500, "reason": "test escrow"},
        headers=auth_header(admin_user),
    )
    assert top_up.status_code in (200, 201), top_up.text

    order = await _create_order(client, publisher)

    # 打手接单前先基于订单建联（PENDING 允许咨询）
    opened = await client.post(
        "/chat/conversations",
        headers=auth_header(booster),
        json={
            "target_user_id": publisher["user"]["id"],
            "order_id": order["id"],
        },
    )
    assert opened.status_code == 200, opened.text
    conversation_id = opened.json()["id"]

    await _complete_order(client, publisher, booster, order["id"])

    status = (
        await client.get(f"/orders/{order['id']}", headers=auth_header(publisher))
    ).json()["status"]
    assert status == OrderStatus.COMPLETED.value

    # 关键断言：已完成订单再次打开会话必须成功，且是同一个会话
    reopened = await client.post(
        "/chat/conversations",
        headers=auth_header(booster),
        json={
            "target_user_id": publisher["user"]["id"],
            "order_id": order["id"],
        },
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["id"] == conversation_id


async def test_active_order_creates_conversation_normally(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
) -> None:
    """进行中订单新建会话行为不变（防回归）。"""
    publisher = registered_user

    top_up = await client.post(
        f"/admin/wallets/{publisher['user']['id']}/adjust",
        json={"amount": 500, "reason": "test escrow"},
        headers=auth_header(admin_user),
    )
    assert top_up.status_code in (200, 201), top_up.text

    order = await _create_order(client, publisher)

    created = await client.post(
        "/chat/conversations",
        headers=auth_header(booster_user),
        json={
            "target_user_id": publisher["user"]["id"],
            "order_id": order["id"],
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["order_id"] == order["id"]

    # 再点一次拿到的是同一个会话（幂等）
    again = await client.post(
        "/chat/conversations",
        headers=auth_header(booster_user),
        json={
            "target_user_id": publisher["user"]["id"],
            "order_id": order["id"],
        },
    )
    assert again.status_code == 200, again.text
    assert again.json()["id"] == created.json()["id"]


async def test_completed_order_without_conversation_is_refused_for_participant(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
) -> None:
    """已完成订单：即使双方是参与者，没聊过也不让新建。"""
    publisher = registered_user

    top_up = await client.post(
        f"/admin/wallets/{publisher['user']['id']}/adjust",
        json={"amount": 500, "reason": "test escrow"},
        headers=auth_header(admin_user),
    )
    assert top_up.status_code in (200, 201), top_up.text

    order = await _create_order(client, publisher)
    await _complete_order(client, publisher, booster_user, order["id"])

    # 双方是订单参与者，但从未建过会话 → 仍拒绝新建
    response = await client.post(
        "/chat/conversations",
        headers=auth_header(booster_user),
        json={
            "target_user_id": publisher["user"]["id"],
            "order_id": order["id"],
        },
    )
    assert response.status_code == 403, response.text
    assert "COMPLETED" in response.json()["detail"]
