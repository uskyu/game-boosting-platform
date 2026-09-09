"""Focused tests for real order image attachments."""
from io import BytesIO

from httpx import AsyncClient
from PIL import Image

from tests.conftest import auth_header


def image_bytes(image_format: str, **kwargs) -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, format=image_format, **kwargs)
    return output.getvalue()


PNG = image_bytes("PNG")
JPEG = image_bytes("JPEG")
WEBP = image_bytes("WEBP")
BMP = image_bytes("BMP")


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
    response = await upload(client, admin_user, order["id"], b"x" * (10 * 1024 * 1024 + 1))
    assert response.status_code == 400
    response = await upload(client, admin_user, order["id"], JPEG, "proof.png", "image/png")
    assert response.status_code == 201
    item = response.json()
    assert item["content_type"] == "image/jpeg"
    assert item["url"].endswith(".jpg")
    response = await upload(client, admin_user, order["id"], b"not image", "proof.exe", "application/octet-stream")
    assert response.status_code == 400


async def test_attachment_normalizes_decodable_formats_and_unknown_mime(client: AsyncClient, admin_user: dict):
    order = await create_order(client, admin_user)
    response = await upload(client, admin_user, order["id"], BMP, "proof.bmp", "image/bmp")
    assert response.status_code == 201
    item = response.json()
    assert item["content_type"] == "image/jpeg"
    assert item["url"].endswith(".jpg")
    response = await upload(client, admin_user, order["id"], WEBP, "proof.unknown", "")
    assert response.status_code == 201
    assert response.json()["content_type"] == "image/jpeg"


async def test_attachment_rejects_empty_and_corrupt_images(client: AsyncClient, admin_user: dict):
    order = await create_order(client, admin_user)
    assert (await upload(client, admin_user, order["id"], b"", "empty.png", "image/png")).status_code == 400
    assert (await upload(client, admin_user, order["id"], PNG[:10], "corrupt.png", "image/png")).status_code == 400


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
