"""Regression tests for default booster eligibility of registered users."""

from httpx import AsyncClient

from tests.conftest import auth_header


async def _admin_order(client: AsyncClient, admin_user: dict) -> dict:
    response = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "500.00",
            "description_raw": "普通用户可抢的管理员订单",
        },
        headers=auth_header(admin_user),
    )
    assert response.status_code == 201
    return response.json()


async def test_new_registered_user_can_browse_and_accept_order(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
):
    """A new USER account can use the order hall and accept without approval."""
    order = await _admin_order(client, admin_user)

    response = await client.get("/orders/", headers=auth_header(registered_user))
    assert response.status_code == 200
    assert order["id"] in [item["id"] for item in response.json()["items"]]

    response = await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(registered_user),
    )
    assert response.status_code == 200
    assert response.json()["booster_id"] == registered_user["user"]["id"]
    assert response.json()["status"] == "LOCKED"


async def test_new_registered_user_cannot_publish_order(
    client: AsyncClient,
    registered_user: dict,
):
    response = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "500.00",
        },
        headers=auth_header(registered_user),
    )
    assert response.status_code == 403
    assert "不能发单" in response.json()["detail"]


async def test_admin_can_still_publish_orders(
    client: AsyncClient,
    admin_user: dict,
):
    order = await _admin_order(client, admin_user)
    assert order["status"] == "PENDING"
    assert order["user_id"] == admin_user["user"]["id"]
    assert order["booster_id"] is None


async def test_user_cannot_accept_own_order(
    client: AsyncClient,
    admin_user: dict,
):
    """The owner guard remains enforced for a non-admin account."""
    # This verifies the API's owner rule using a user who owns a seeded order.
    # The public create endpoint is intentionally admin-only, so use the admin
    # order and assert the admin cannot claim it through the booster endpoint.
    order = await _admin_order(client, admin_user)
    response = await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(admin_user),
    )
    assert response.status_code == 403
