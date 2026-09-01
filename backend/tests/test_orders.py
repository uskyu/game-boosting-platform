"""Order lifecycle and payment tests."""

from httpx import AsyncClient
from tests.conftest import auth_header


async def _create_order(client: AsyncClient, user_data: dict) -> dict:
    """Helper: create a standard test order."""
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "500.00",
            "description_raw": "钻石上王者",
        },
        headers=auth_header(user_data),
    )
    assert resp.status_code == 201
    return resp.json()


async def test_create_order(client: AsyncClient, registered_user: dict):
    order = await _create_order(client, registered_user)
    assert order["status"] == "PENDING"
    assert order["game_name"] == "王者荣耀"
    assert order["payment_status"] == "UNPAID"


async def test_create_order_no_auth(client: AsyncClient):
    resp = await client.post("/orders/create", json={
        "game_name": "王者荣耀",
        "current_rank": "钻石",
        "target_rank": "王者",
        "price": "500.00",
    })
    assert resp.status_code == 401


async def test_accept_order(
    client: AsyncClient, registered_user: dict, booster_user: dict
):
    order = await _create_order(client, registered_user)
    resp = await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(booster_user),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "LOCKED"


async def test_accept_order_non_booster(
    client: AsyncClient, registered_user: dict
):
    order = await _create_order(client, registered_user)

    # Register a second regular user
    resp = await client.post("/auth/register", json={
        "email": "regular2@example.com",
        "username": "Regular2",
        "password": "RegularPass1",
    })
    regular = resp.json()

    resp = await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(regular),
    )
    assert resp.status_code == 403


async def test_deliver_order(
    client: AsyncClient, registered_user: dict, booster_user: dict
):
    """Booster delivers order -> status becomes DELIVERED."""
    order = await _create_order(client, registered_user)
    await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(booster_user),
    )
    resp = await client.put(
        f"/orders/{order['id']}/deliver",
        headers=auth_header(booster_user),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "DELIVERED"


async def test_confirm_order(
    client: AsyncClient, registered_user: dict, booster_user: dict
):
    """Customer confirms delivered order -> status becomes COMPLETED."""
    order = await _create_order(client, registered_user)
    await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(booster_user),
    )
    await client.put(
        f"/orders/{order['id']}/deliver",
        headers=auth_header(booster_user),
    )
    resp = await client.put(
        f"/orders/{order['id']}/confirm",
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "COMPLETED"


async def test_pay_order(client: AsyncClient, registered_user: dict):
    order = await _create_order(client, registered_user)
    resp = await client.put(
        f"/orders/{order['id']}/pay",
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["payment_status"] == "PAID"
    assert data["paid_at"] is not None
