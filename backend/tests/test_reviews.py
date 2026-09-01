"""Review system tests."""

from httpx import AsyncClient
from tests.conftest import auth_header


async def _create_completed_order(
    client: AsyncClient, user_data: dict, booster_data: dict
) -> dict:
    """Helper: create an order and advance it to COMPLETED (accept -> deliver -> confirm)."""
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "原神",
            "current_rank": "冒险等级30",
            "target_rank": "冒险等级50",
            "price": "300.00",
        },
        headers=auth_header(user_data),
    )
    order = resp.json()

    await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(booster_data),
    )
    await client.put(
        f"/orders/{order['id']}/deliver",
        headers=auth_header(booster_data),
    )
    await client.put(
        f"/orders/{order['id']}/confirm",
        headers=auth_header(user_data),
    )
    return order


async def test_create_review(
    client: AsyncClient, registered_user: dict, booster_user: dict
):
    order = await _create_completed_order(client, registered_user, booster_user)
    resp = await client.post(
        f"/orders/{order['id']}/reviews",
        json={"rating": 5, "content": "非常好的代练"},
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["rating"] == 5
    assert data["reviewer_id"] == registered_user["user"]["id"]


async def test_review_non_completed_order(
    client: AsyncClient, registered_user: dict
):
    # Create a PENDING order (not completed)
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "原神",
            "current_rank": "30",
            "target_rank": "50",
            "price": "300.00",
        },
        headers=auth_header(registered_user),
    )
    order = resp.json()

    resp = await client.post(
        f"/orders/{order['id']}/reviews",
        json={"rating": 5},
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 400


async def test_duplicate_review(
    client: AsyncClient, registered_user: dict, booster_user: dict
):
    order = await _create_completed_order(client, registered_user, booster_user)

    await client.post(
        f"/orders/{order['id']}/reviews",
        json={"rating": 5},
        headers=auth_header(registered_user),
    )
    resp = await client.post(
        f"/orders/{order['id']}/reviews",
        json={"rating": 4},
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 400


async def test_update_review(
    client: AsyncClient, registered_user: dict, booster_user: dict
):
    order = await _create_completed_order(client, registered_user, booster_user)

    await client.post(
        f"/orders/{order['id']}/reviews",
        json={"rating": 3, "content": "一般"},
        headers=auth_header(registered_user),
    )

    resp = await client.put(
        f"/orders/{order['id']}/reviews",
        json={"rating": 5, "content": "改评价了，非常好"},
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 200
    assert resp.json()["rating"] == 5


async def test_get_user_reviews(
    client: AsyncClient, registered_user: dict, booster_user: dict
):
    order = await _create_completed_order(client, registered_user, booster_user)

    await client.post(
        f"/orders/{order['id']}/reviews",
        json={"rating": 4, "content": "不错"},
        headers=auth_header(registered_user),
    )

    booster_id = booster_user["user"]["id"]
    resp = await client.get(f"/users/{booster_id}/reviews")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert data["average_rating"] is not None
