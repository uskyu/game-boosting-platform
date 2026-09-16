"""老板开关：开启"结单必须上传完成截图"后交付强校验 + 上传图自动压缩。

- require_delivery_image=true 时无图 deliver → 400；上传 ≥1 张后 → 200
- 开关随创建/更新字段落库并回显
- file_service：>2MB 上传图自动重编码（最长边 2000、体积显著变小）；≤2MB 原样透传
"""

import io

from fastapi.testclient import TestClient  # noqa: F401 (类型参考，实际用 httpx client)
from PIL import Image
from httpx import AsyncClient

from app.services.file_service import _validate_and_normalize_image
from tests.conftest import auth_header


async def _make_order(client: AsyncClient, admin_user: dict, *, require_image: bool = True) -> dict:
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "100.00",
            "require_delivery_image": require_image,
        },
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _accept(client: AsyncClient, user: dict, order_id: int) -> None:
    resp = await client.put(f"/orders/{order_id}/accept", headers=auth_header(user))
    assert resp.status_code == 200, resp.text


async def _deliver(client: AsyncClient, user: dict, order_id: int):
    return await client.put(
        f"/orders/{order_id}/deliver",
        json={"delivery_note": "done"},
        headers=auth_header(user),
    )


async def test_deliver_requires_image_when_enabled(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    order = await _make_order(client, admin_user, require_image=True)
    assert order["require_delivery_image"] is True
    await _accept(client, booster_user, order["id"])

    resp = await _deliver(client, booster_user, order["id"])
    assert resp.status_code == 400, resp.text
    assert "完成截图" in resp.json()["detail"]

    buf = io.BytesIO()
    Image.new("RGB", (80, 60), "white").save(buf, format="PNG")
    buf.seek(0)
    upload = await client.post(
        f"/orders/{order['id']}/deliver-attachments",
        files={"attachment": ("shot.png", buf, "image/png")},
        headers=auth_header(booster_user),
    )
    assert upload.status_code == 201, upload.text

    resp = await _deliver(client, booster_user, order["id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["my_claim"]["status"] == "DELIVERED"


async def test_deliver_free_when_toggle_off(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    order = await _make_order(client, admin_user, require_image=False)
    assert order["require_delivery_image"] is False
    await _accept(client, booster_user, order["id"])

    resp = await _deliver(client, booster_user, order["id"])
    assert resp.status_code == 200, resp.text


async def test_update_can_toggle_requirement(
    client: AsyncClient, admin_user: dict
):
    order = await _make_order(client, admin_user, require_image=False)
    resp = await client.put(
        f"/orders/{order['id']}",
        json={"require_delivery_image": True},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["require_delivery_image"] is True


def _big_jpeg_bytes(min_bytes: int = 2 * 1024 * 1024 + 64 * 1024) -> bytes:
    """生成一张必然超过 2MB 的 JPEG（PIL 原生高斯噪声，压缩率低）。"""
    image = Image.effect_noise((3600, 2400), 30).convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=90)
    data = buf.getvalue()
    assert len(data) > min_bytes, len(data)
    return data


def test_big_upload_is_compressed_and_capped():
    data = _big_jpeg_bytes()
    out, suffix, mime = _validate_and_normalize_image(data, "image/jpeg", ".jpg")
    assert len(out) < 1024 * 1024, f"compressed={len(out)}"
    assert (suffix, mime) == (".jpg", "image/jpeg")
    with Image.open(io.BytesIO(out)) as image:
        assert max(image.width, image.height) <= 2000


def test_small_upload_passthrough_unchanged():
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), "steelblue").save(buf, format="JPEG", quality=90)
    data = buf.getvalue()
    out, suffix, mime = _validate_and_normalize_image(data, "image/jpeg", ".jpg")
    assert out == data
    assert (suffix, mime) == (".jpg", "image/jpeg")
