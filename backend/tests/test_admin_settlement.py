"""Regression tests for administrator order completion settlement."""

from decimal import Decimal

from httpx import AsyncClient

from tests.conftest import auth_header


async def _create_order(client: AsyncClient, user: dict, price: str = "500.00") -> dict:
    response = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": price,
        },
        headers=auth_header(user),
    )
    assert response.status_code == 201
    return response.json()


async def test_admin_completion_credits_assigned_booster(
    client: AsyncClient,
    registered_user: dict,
    booster_user: dict,
    admin_user: dict,
):
    order = await _create_order(client, registered_user)
    response = await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(booster_user),
    )
    assert response.status_code == 200

    response = await client.put(
        f"/admin/orders/{order['id']}/intervene",
        json={"action": "COMPLETED", "reason": "管理员解决争议"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"

    response = await client.get("/wallet", headers=auth_header(booster_user))
    assert response.status_code == 200
    wallet = response.json()
    assert Decimal(str(wallet["available_balance"])) == Decimal("500.00")
    assert Decimal(str(wallet["total_income"])) == Decimal("500.00")

    response = await client.get(
        "/wallet/transactions", headers=auth_header(booster_user)
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1


async def test_repeated_admin_completion_does_not_double_credit(
    client: AsyncClient,
    registered_user: dict,
    booster_user: dict,
    admin_user: dict,
):
    order = await _create_order(client, registered_user)
    response = await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(booster_user),
    )
    assert response.status_code == 200

    payload = {"action": "COMPLETED", "reason": "管理员完结"}
    for _ in range(2):
        response = await client.put(
            f"/admin/orders/{order['id']}/intervene",
            json=payload,
            headers=auth_header(admin_user),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "COMPLETED"

    response = await client.get("/wallet", headers=auth_header(booster_user))
    assert Decimal(str(response.json()["available_balance"])) == Decimal("500.00")
    assert Decimal(str(response.json()["total_income"])) == Decimal("500.00")

    response = await client.get(
        "/wallet/transactions", headers=auth_header(booster_user)
    )
    assert response.json()["total"] == 1


async def test_admin_completion_without_booster_is_safe(
    client: AsyncClient,
    registered_user: dict,
    admin_user: dict,
):
    order = await _create_order(client, registered_user)
    response = await client.put(
        f"/admin/orders/{order['id']}/intervene",
        json={"action": "COMPLETED", "reason": "无接单人，管理员处理"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"


async def test_normal_customer_confirmation_still_settles(
    client: AsyncClient,
    registered_user: dict,
    booster_user: dict,
):
    order = await _create_order(client, registered_user)
    for action in ("accept", "deliver"):
        response = await client.put(
            f"/orders/{order['id']}/{action}",
            headers=auth_header(booster_user),
        )
        assert response.status_code == 200

    response = await client.put(
        f"/orders/{order['id']}/confirm",
        headers=auth_header(registered_user),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"

    response = await client.get("/wallet", headers=auth_header(booster_user))
    assert Decimal(str(response.json()["total_income"])) == Decimal("500.00")
