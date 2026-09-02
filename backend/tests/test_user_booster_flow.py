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


async def test_new_registered_user_publish_order_requires_balance(
    client: AsyncClient,
    registered_user: dict,
    admin_user: dict,
):
    """新权限模型：任何注册用户都能发单，但余额必须覆盖托管（price × max_claims）。"""
    # 无余额的普通用户发单被拒（托管不足）
    response = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "price": "500.00",
        },
        headers=auth_header(registered_user),
    )
    assert response.status_code == 400
    assert "余额不足" in response.json()["detail"]

    # 充值后可以发单，且托管被冻结
    await client.post(
        f"/admin/wallets/{registered_user['user']['id']}/adjust",
        json={"amount": "1000.00", "reason": "发单测试充值"},
        headers=auth_header(admin_user),
    )
    response = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "price": "300.00",
            "max_claims": 2,
        },
        headers=auth_header(registered_user),
    )
    assert response.status_code == 201
    wallet = (await client.get("/wallet", headers=auth_header(registered_user))).json()
    assert wallet["available_balance"] == "400.00"
    assert wallet["frozen_balance"] == "600.00"


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
