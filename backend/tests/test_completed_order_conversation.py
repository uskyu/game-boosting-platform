"""已完成/已取消订单的联系能力。

终态订单曾经整单拒绝建联，导致两处坏体验：
1. 订单进行期聊过的会话，完结后在详情页打不开（点「联系打手」直接 403）；
2. 完结时才想联系对方（售后、复购）则完全无法发起。
现在的规则：终态订单只保留归档校验，联络资格由订单参与者集合判定——
发布者与参与打手可随时互聊，与订单无关的打手仍被拒绝。
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


async def _register_booster(
    client: AsyncClient,
    db_session,
    make_captcha,
    *,
    email: str,
    username: str,
) -> dict:
    """注册一个新账号并提升为打手，返回带令牌的登录响应。"""
    from app.models.user import User, UserRole

    password = "BoostPass123"
    registered = await client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "password": password,
            **make_captcha(),
        },
    )
    assert registered.status_code in (200, 201), registered.text

    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    user.role = UserRole.BOOSTER
    user.booster_quota = 5
    await db_session.commit()

    login = await client.post(
        "/auth/login", json={"email": email, "password": password}
    )
    assert login.status_code == 200, login.text
    return login.json()


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


async def test_owner_can_contact_booster_after_completion(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
) -> None:
    """老板实测场景：订单已完结且双方从未聊过，点「联系打手」必须能建联。"""
    publisher = registered_user

    top_up = await client.post(
        f"/admin/wallets/{publisher['user']['id']}/adjust",
        json={"amount": 500, "reason": "test escrow"},
        headers=auth_header(admin_user),
    )
    assert top_up.status_code in (200, 201), top_up.text

    order = await _create_order(client, publisher)
    await _complete_order(client, publisher, booster_user, order["id"])

    # 发布者（老板）侧发起：按老板的实际点击路径，order_id + 打手 id
    created = await client.post(
        "/chat/conversations",
        headers=auth_header(publisher),
        json={
            "target_user_id": booster_user["user"]["id"],
            "order_id": order["id"],
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["order_id"] == order["id"]

    # 打手侧反向再点一次：拿到同一个会话（幂等且双向）
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


async def test_uninvolved_booster_cannot_contact_owner_after_completion(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
    db_session,
    make_captcha,
) -> None:
    """防骚扰：没参与过该订单的打手，不能借已完结订单联系发布者。"""
    publisher = registered_user

    top_up = await client.post(
        f"/admin/wallets/{publisher['user']['id']}/adjust",
        json={"amount": 500, "reason": "test escrow"},
        headers=auth_header(admin_user),
    )
    assert top_up.status_code in (200, 201), top_up.text

    order = await _create_order(client, publisher)
    await _complete_order(client, publisher, booster_user, order["id"])

    stranger = await _register_booster(
        client,
        db_session,
        make_captcha,
        email="stranger_booster@example.com",
        username="StrangerBooster",
    )

    response = await client.post(
        "/chat/conversations",
        headers=auth_header(stranger),
        json={
            "target_user_id": publisher["user"]["id"],
            "order_id": order["id"],
        },
    )
    assert response.status_code == 403, response.text
