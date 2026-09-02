"""Admin order assignment tests."""

from httpx import AsyncClient
from sqlalchemy import select

from app.models.user import User, UserRole
from tests.conftest import auth_header


async def _create_order(client: AsyncClient, user_data: dict, price: str = "500.00") -> dict:
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": price,
        },
        headers=auth_header(user_data),
    )
    assert resp.status_code == 201
    return resp.json()


async def test_assign_order_locks_order(
    client: AsyncClient, registered_user: dict, booster_user: dict, admin_user: dict
):
    """Admin assign sets booster_id, status LOCKED and locked_at."""
    order = await _create_order(client, admin_user)
    booster_id = booster_user["user"]["id"]

    resp = await client.put(
        f"/admin/orders/{order['id']}/assign",
        json={"booster_id": booster_id, "reason": "急单优先指派"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "LOCKED"
    assert data["booster_id"] == booster_id
    assert data["locked_at"] is not None


async def test_assign_regular_user_as_booster(
    client: AsyncClient, registered_user: dict, admin_user: dict
):
    """Any registered non-admin user acts as a booster and can be assigned."""
    order = await _create_order(client, admin_user)

    # A second regular user: serialized as BOOSTER (non-admin = booster)
    resp = await client.post("/auth/register", json={
        "email": "plain@example.com",
        "username": "PlainUser",
        "password": "PlainPass1",
    })
    plain = resp.json()
    assert plain["user"]["role"] == "BOOSTER"

    resp = await client.put(
        f"/admin/orders/{order['id']}/assign",
        json={"booster_id": plain["user"]["id"]},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "LOCKED"
    assert data["booster_id"] == plain["user"]["id"]


async def test_assign_rejects_admin_account(
    client: AsyncClient, admin_user: dict
):
    """Admin accounts cannot be assigned as boosters."""
    order = await _create_order(client, admin_user)

    resp = await client.put(
        f"/admin/orders/{order['id']}/assign",
        json={"booster_id": admin_user["user"]["id"]},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 400


async def test_assign_rejects_non_pending_order(
    client: AsyncClient, registered_user: dict, booster_user: dict, admin_user: dict
):
    """Orders that are already locked/assigned cannot be assigned again."""
    order = await _create_order(client, admin_user)
    booster_id = booster_user["user"]["id"]

    resp = await client.put(
        f"/admin/orders/{order['id']}/assign",
        json={"booster_id": booster_id},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200

    # Assign the same order to the booster again -> not PENDING anymore
    resp = await client.put(
        f"/admin/orders/{order['id']}/assign",
        json={"booster_id": booster_id},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 400


async def test_assign_rejects_quota_full(
    client: AsyncClient,
    registered_user: dict,
    admin_user: dict,
    db_session,
):
    """A booster with no free quota cannot take more orders."""
    order = await _create_order(client, admin_user)

    # Create a booster with zero quota
    resp = await client.post("/auth/register", json={
        "email": "zeroboost@example.com",
        "username": "ZeroBoost",
        "password": "ZeroPass1",
    })
    zero = resp.json()
    result = await db_session.execute(
        select(User).where(User.email == "zeroboost@example.com")
    )
    zero_user = result.scalar_one()
    zero_user.role = UserRole.BOOSTER
    zero_user.booster_quota = 0
    await db_session.commit()

    resp = await client.put(
        f"/admin/orders/{order['id']}/assign",
        json={"booster_id": zero_user.id},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 400


async def test_assign_rejects_inactive_booster(
    client: AsyncClient,
    registered_user: dict,
    admin_user: dict,
    db_session,
):
    """An inactive booster cannot be assigned orders."""
    order = await _create_order(client, admin_user)

    resp = await client.post("/auth/register", json={
        "email": "inactive@example.com",
        "username": "InactiveBoost",
        "password": "InactPass1",
    })
    inactive = resp.json()
    result = await db_session.execute(
        select(User).where(User.email == "inactive@example.com")
    )
    inactive_user = result.scalar_one()
    inactive_user.role = UserRole.BOOSTER
    inactive_user.booster_quota = 5
    inactive_user.is_active = False
    await db_session.commit()

    resp = await client.put(
        f"/admin/orders/{order['id']}/assign",
        json={"booster_id": inactive_user.id},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 400


async def test_assign_requires_admin(
    client: AsyncClient, registered_user: dict, booster_user: dict, admin_user: dict
):
    """Non-admin users (including boosters) cannot call the assign endpoint."""
    order = await _create_order(client, admin_user)

    resp = await client.put(
        f"/admin/orders/{order['id']}/assign",
        json={"booster_id": booster_user["user"]["id"]},
        headers=auth_header(booster_user),
    )
    assert resp.status_code == 403


async def test_assign_nonexistent_order_404(client: AsyncClient, admin_user: dict, booster_user: dict):
    resp = await client.put(
        "/admin/orders/999999/assign",
        json={"booster_id": booster_user["user"]["id"]},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 404


async def test_assign_nonexistent_booster_404(
    client: AsyncClient, registered_user: dict, admin_user: dict
):
    order = await _create_order(client, admin_user)
    resp = await client.put(
        f"/admin/orders/{order['id']}/assign",
        json={"booster_id": 999999},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 404
