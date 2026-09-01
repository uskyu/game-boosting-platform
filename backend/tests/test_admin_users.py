"""Administrator user management API tests."""

from httpx import AsyncClient

from tests.conftest import auth_header


async def test_admin_can_list_and_view_users(client: AsyncClient, registered_user: dict, admin_user: dict):
    response = await client.get("/admin/users", headers=auth_header(admin_user))
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert all("hashed_password" not in item for item in data["items"])

    user_id = registered_user["user"]["id"]
    response = await client.get(f"/admin/users/{user_id}", headers=auth_header(admin_user))
    assert response.status_code == 200
    assert response.json()["id"] == user_id
    assert "hashed_password" not in response.json()


async def test_non_admin_cannot_manage_users(client: AsyncClient, registered_user: dict):
    response = await client.get("/admin/users", headers=auth_header(registered_user))
    assert response.status_code == 403


async def test_admin_can_update_and_reset_password(
    client: AsyncClient, registered_user: dict, admin_user: dict
):
    user_id = registered_user["user"]["id"]
    response = await client.patch(
        f"/admin/users/{user_id}",
        json={"username": "UpdatedName", "is_verified": True, "booster_quota": 3},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
    assert response.json()["username"] == "UpdatedName"

    response = await client.post(
        f"/admin/users/{user_id}/reset-password",
        json={"password": "NewStrongPass1"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
    assert "password" not in response.json()


async def test_admin_cannot_disable_self(client: AsyncClient, admin_user: dict):
    user_id = admin_user["user"]["id"]
    response = await client.post(
        f"/admin/users/{user_id}/status",
        json={"is_active": False},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 400


async def test_admin_adjust_balance_records_transaction(
    client: AsyncClient, registered_user: dict, admin_user: dict
):
    user_id = registered_user["user"]["id"]
    response = await client.post(
        f"/admin/users/{user_id}/adjust-balance",
        json={"amount": "12.50", "reason": "测试补款"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
    assert response.json()["available"] == "12.50"
    assert response.json()["transaction_id"]
