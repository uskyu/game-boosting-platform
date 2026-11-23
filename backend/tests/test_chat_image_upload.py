"""聊天图片上传回归：确保 send_image_message 使用存储门面且不再 NameError。"""

from io import BytesIO

from httpx import AsyncClient
from PIL import Image

from tests.conftest import auth_header


def _png_bytes() -> bytes:
    out = BytesIO()
    Image.new("RGB", (2, 2), "red").save(out, format="PNG")
    return out.getvalue()


async def test_chat_image_upload_uses_storage_service(
    client: AsyncClient,
    registered_user: dict,
    booster_user: dict,
):
    """发布人与打手建会话后发图：应 201，消息内容为 /uploads/chat/...。"""
    created = await client.post(
        "/chat/conversations",
        headers=auth_header(registered_user),
        json={"target_user_id": booster_user["user"]["id"], "order_id": None},
    )
    assert created.status_code in (200, 201), created.text
    conversation_id = created.json()["id"]

    resp = await client.post(
        f"/chat/conversations/{conversation_id}/upload",
        headers=auth_header(registered_user),
        files={"file": ("probe.png", _png_bytes(), "image/png")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["message_type"] == "IMAGE"
    assert body["content"].startswith("/uploads/chat/")
