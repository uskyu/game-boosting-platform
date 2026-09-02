"""Focused tests for real order image attachments."""
from io import BytesIO

from httpx import AsyncClient

from tests.conftest import auth_header


PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 32
JPEG = b"\xff\xd8\xff" + b"x" * 32


async def create_order(client: AsyncClient, user: dict) -> dict:
    response = await client.post(
        "/orders/create",
        json={"game_name": "王者荣耀", "current_rank": "钻石", "target_rank": "王者", "price": "500"},
        headers=auth_header(user),
    )
    assert response.status_code == 201
    return response.json()


async def upload(client: AsyncClient, user: dict, order_id: int, data=PNG, name="proof.png", content_type="image/png"):
    return await client.post(
        f"/orders/{order_id}/attachments",
        files={"attachment": (name, BytesIO(data), content_type)},
        headers=auth_header(user),
    )


async def test_upload_success_and_delete(client: AsyncClient, admin_user: dict):
    order = await create_order(client, admin_user)
    response = await upload(client, admin_user, order["id"])
    assert response.status_code == 201
    item = response.json()
    assert item["url"].startswith("/uploads/orders/")
    assert item["name"] == "proof.png"
    assert item["size"] == len(PNG)
    assert item["content_type"] == "image/png"

    response = await client.delete(
        f"/orders/{order['id']}/attachments/0", headers=auth_header(admin_user)
    )
    assert response.status_code == 200
    assert response.json()["attachments"] == []


async def test_attachment_requires_owner_or_admin(client: AsyncClient, admin_user: dict, booster_user: dict):
    order = await create_order(client, admin_user)
    response = await upload(client, booster_user, order["id"])
    assert response.status_code == 403
    response = await upload(client, admin_user, order["id"])
    assert response.status_code == 201
    response = await client.delete(
        f"/orders/{order['id']}/attachments/0", headers=auth_header(admin_user)
    )
    assert response.status_code == 200


async def test_attachment_rejects_oversized_and_invalid_format(client: AsyncClient, admin_user: dict):
    order = await create_order(client, admin_user)
    response = await upload(client, admin_user, order["id"], b"x" * (5 * 1024 * 1024 + 1))
    assert response.status_code == 400
    response = await upload(client, admin_user, order["id"], JPEG, "proof.png", "image/png")
    assert response.status_code == 400
    response = await upload(client, admin_user, order["id"], b"not image", "proof.exe", "application/octet-stream")
    assert response.status_code == 400


async def test_attachment_limit_is_five(client: AsyncClient, admin_user: dict):
    order = await create_order(client, admin_user)
    for index in range(5):
        response = await upload(client, admin_user, order["id"], name=f"{index}.png")
        assert response.status_code == 201
    response = await upload(client, admin_user, order["id"], name="sixth.png")
    assert response.status_code == 400


async def test_attachment_delete_index_out_of_range(client: AsyncClient, admin_user: dict):
    order = await create_order(client, admin_user)
    response = await client.delete(
        f"/orders/{order['id']}/attachments/0", headers=auth_header(admin_user)
    )
    assert response.status_code == 400
