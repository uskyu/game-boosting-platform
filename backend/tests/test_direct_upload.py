"""图片直传（浏览器 -> COS）端点测试。

全部用例不访问网络：``storage_service.presign_put`` / ``object_info`` 用
替身模拟命中/不存在/超限，既不依赖真实桶也不会误连生产。

覆盖：
* LOCAL 模式 presign 返回 409，前端据此回退 multipart；
* 非订单发布人申请订单附件直传被 403；
* 附件已满 5 张时不发签名；
* commit 拒绝跨 scope 的 key、类型不匹配、对象不存在、超过 10MB；
* commit 成功后写入订单附件，且 URL 保持 /uploads/<key> 契约。
"""

import pytest
from httpx import AsyncClient

from app.services import storage_service
from tests.conftest import auth_header

_PNG = "image/png"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def fake_cos(monkeypatch):
    """把 COS 侧换成进程内替身：presign 直接给固定 URL，object_info/魔数可控。"""
    state = {
        "exists": True,
        "size": 1024,
        "content_type": _PNG,
        "head": _PNG_MAGIC + b"probe-bytes",
        "deleted": [],
    }

    async def _presign_put(key, content_type, db, *, expires=600):
        return f"https://fake-cos.example/{key}?sign=stub"

    async def _object_info(key, db):
        if not state["exists"]:
            return None
        return state["size"], state["content_type"]

    async def _read_prefix(key, db, *, length=32):
        return state["head"]

    async def _delete(key, db):
        state["deleted"].append(key)

    monkeypatch.setattr(storage_service, "presign_put", _presign_put)
    monkeypatch.setattr(storage_service, "object_info", _object_info)
    monkeypatch.setattr(storage_service, "read_prefix", _read_prefix)
    monkeypatch.setattr(storage_service, "delete", _delete)
    return state


async def _fund(client: AsyncClient, admin_user: dict, user: dict, amount: int = 1000) -> None:
    resp = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": amount, "reason": "direct upload test"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code in (200, 201), resp.text


@pytest.fixture
async def publisher(client: AsyncClient, admin_user: dict, registered_user: dict) -> dict:
    """发布人需要有余额托管（否则 create_order 直接 400）。"""
    await _fund(client, admin_user, registered_user)
    return registered_user


async def _create_order(client: AsyncClient, publisher: dict, **overrides) -> dict:
    body = {
        "game_name": "王者荣耀",
        "price": "10.00",
        "title": "直传测试订单",
        "boss_contact": "BOSS-DIRECT",
        **overrides,
    }
    resp = await client.post("/orders/create", json=body, headers=auth_header(publisher))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_presign_requires_cos_mode(
    client: AsyncClient, publisher: dict
):
    """LOCAL 模式（默认）返回 409，前端必须回退到 multipart。"""
    order = await _create_order(client, publisher)
    resp = await client.post(
        "/uploads/presign",
        json={
            "scope": "order_attachment",
            "order_id": order["id"],
            "filename": "a.png",
            "content_type": _PNG,
            "size": 1234,
        },
        headers=auth_header(publisher),
    )
    assert resp.status_code == 409, resp.text
    assert "直传" in resp.json()["detail"]


async def test_presign_rejects_non_owner(
    client: AsyncClient, publisher: dict, booster_user: dict, fake_cos
):
    """别人的订单不能申请直传。"""
    order = await _create_order(client, publisher)
    resp = await client.post(
        "/uploads/presign",
        json={
            "scope": "order_attachment",
            "order_id": order["id"],
            "filename": "a.png",
            "content_type": _PNG,
            "size": 1234,
        },
        headers=auth_header(booster_user),
    )
    assert resp.status_code == 403, resp.text


async def test_presign_rejects_too_many_attachments(
    client: AsyncClient, db_session, publisher: dict, fake_cos
):
    """已满 5 张时不再签发（避免传完才发现超限）。"""
    from sqlalchemy import update

    from app.models.order import Order

    order = await _create_order(client, publisher)
    await db_session.execute(
        update(Order)
        .where(Order.id == order["id"])
        .values(
            attachments=[
                {
                    "url": f"/uploads/orders/{i}.png",
                    "name": f"{i}.png",
                    "size": 10,
                    "content_type": _PNG,
                }
                for i in range(5)
            ]
        )
    )
    await db_session.commit()

    resp = await client.post(
        "/uploads/presign",
        json={
            "scope": "order_attachment",
            "order_id": order["id"],
            "filename": "a.png",
            "content_type": _PNG,
            "size": 1234,
        },
        headers=auth_header(publisher),
    )
    assert resp.status_code == 400, resp.text
    assert "5张" in resp.json()["detail"]


async def test_presign_returns_key_and_url(
    client: AsyncClient, publisher: dict, fake_cos
):
    order = await _create_order(client, publisher)
    resp = await client.post(
        "/uploads/presign",
        json={
            "scope": "order_attachment",
            "order_id": order["id"],
            "filename": "截图.PNG",
            "content_type": "image/png; charset=binary",
            "size": 1234,
        },
        headers=auth_header(publisher),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["key"].startswith("orders/")
    assert body["key"].endswith(".png")
    assert body["upload_url"].startswith("https://fake-cos.example/")
    assert body["headers"]["Content-Type"] == _PNG


async def test_commit_rejects_foreign_key(
    client: AsyncClient, publisher: dict, fake_cos
):
    order = await _create_order(client, publisher)
    resp = await client.post(
        "/uploads/commit",
        json={
            "scope": "order_attachment",
            "order_id": order["id"],
            "key": "deliveries/abc.png",
            "name": "a.png",
            "content_type": _PNG,
        },
        headers=auth_header(publisher),
    )
    assert resp.status_code == 400, resp.text
    assert "key" in resp.json()["detail"]


async def test_commit_rejects_missing_object(
    client: AsyncClient, publisher: dict, fake_cos
):
    fake_cos["exists"] = False
    order = await _create_order(client, publisher)
    resp = await client.post(
        "/uploads/commit",
        json={
            "scope": "order_attachment",
            "order_id": order["id"],
            "key": "orders/abc.png",
            "name": "a.png",
            "content_type": _PNG,
        },
        headers=auth_header(publisher),
    )
    assert resp.status_code == 400, resp.text
    assert "未找到" in resp.json()["detail"]


async def test_commit_rejects_oversized_object(
    client: AsyncClient, publisher: dict, fake_cos
):
    fake_cos["size"] = 11 * 1024 * 1024
    order = await _create_order(client, publisher)
    resp = await client.post(
        "/uploads/commit",
        json={
            "scope": "order_attachment",
            "order_id": order["id"],
            "key": "orders/abc.png",
            "name": "a.png",
            "content_type": _PNG,
        },
        headers=auth_header(publisher),
    )
    assert resp.status_code == 400, resp.text
    assert "10MB" in resp.json()["detail"]


async def test_commit_rejects_type_mismatch(
    client: AsyncClient, publisher: dict, fake_cos
):
    fake_cos["content_type"] = "application/pdf"
    order = await _create_order(client, publisher)
    resp = await client.post(
        "/uploads/commit",
        json={
            "scope": "order_attachment",
            "order_id": order["id"],
            "key": "orders/abc.png",
            "name": "a.png",
            "content_type": _PNG,
        },
        headers=auth_header(publisher),
    )
    assert resp.status_code == 400, resp.text
    assert "类型不匹配" in resp.json()["detail"]


async def test_commit_appends_attachment(
    client: AsyncClient, publisher: dict, fake_cos
):
    order = await _create_order(client, publisher)
    resp = await client.post(
        "/uploads/commit",
        json={
            "scope": "order_attachment",
            "order_id": order["id"],
            "key": "orders/abc.png",
            "name": "截图.png",
            "content_type": _PNG,
        },
        headers=auth_header(publisher),
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()
    assert item["url"] == "/uploads/orders/abc.png"
    assert item["size"] == 1024
    assert item["content_type"] == _PNG

    detail = await client.get(f"/orders/{order['id']}", headers=auth_header(publisher))
    assert detail.status_code == 200, detail.text
    assert any(a["url"] == "/uploads/orders/abc.png" for a in detail.json()["attachments"] or [])


async def test_commit_rejects_non_image_content(
    client: AsyncClient, publisher: dict, fake_cos
):
    """魔数不是 PNG 时拒绝落地，并删除脏对象（直传没过 PIL 解码）。"""
    fake_cos["head"] = b"%PDF-1.7 fake payload"
    order = await _create_order(client, publisher)
    resp = await client.post(
        "/uploads/commit",
        json={
            "scope": "order_attachment",
            "order_id": order["id"],
            "key": "orders/bad.png",
            "name": "bad.png",
            "content_type": _PNG,
        },
        headers=auth_header(publisher),
    )
    assert resp.status_code == 400, resp.text
    assert "图片内容无效" in resp.json()["detail"]
    assert "orders/bad.png" in fake_cos["deleted"]
