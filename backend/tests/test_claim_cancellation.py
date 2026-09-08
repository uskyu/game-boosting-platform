"""Order cancellation must terminate unfinished claims (CANCELLED lifecycle).

后台/发布人取消订单后，打手的未结算报名名额同步置为 CANCELLED，
"我的接单"不再显示进行中；已结算名额不受影响。
"""

from httpx import AsyncClient

from tests.conftest import auth_header


async def _register(client: AsyncClient, email: str, username: str) -> dict:
    from app.services import captcha_service
    captcha_id, _ = captcha_service.create()
    code, _ = captcha_service._store[captcha_id]
    resp = await client.post(
        "/auth/register",
        json={"email": email, "username": username, "password": "Passw0rd123",
              "captcha_id": captcha_id, "captcha_code": code},
    )
    assert resp.status_code in (200, 201)
    return resp.json()


async def _create_order(client: AsyncClient, admin_user: dict, max_claims: int = 1) -> dict:
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "100.00",
            "max_claims": max_claims,
        },
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201
    return resp.json()


async def test_cancel_marks_claimed_claims_cancelled(client: AsyncClient, admin_user: dict):
    order = await _create_order(client, admin_user)
    order_id = order["id"]
    booster = await _register(client, "cancel-a@example.com", "CancelBoosterA")

    resp = await client.put(f"/orders/{order_id}/accept", headers=auth_header(booster))
    assert resp.status_code == 200
    assert resp.json()["status"] == "LOCKED"

    resp = await client.put(f"/orders/{order_id}/cancel", headers=auth_header(admin_user))
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"

    # 打手的报名名额变为 CANCELLED，且可用状态筛选拿到
    resp = await client.get(
        "/orders/claims/mine?status=CANCELLED", headers=auth_header(booster)
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == "CANCELLED"
    assert items[0]["order"]["status"] == "CANCELLED"

    resp = await client.get(
        "/orders/claims/mine?status=CLAIMED", headers=auth_header(booster)
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0

    # 已取消的订单/名额不能再交付（订单级或名额级守卫任一生效，均 400）
    resp = await client.put(
        f"/orders/{order_id}/deliver",
        json={"delivery_note": "晚了"},
        headers=auth_header(booster),
    )
    assert resp.status_code == 400

    # 已取消的名额不能上传/修改交付附件（名额级守卫）
    data = {"attachment": ("x.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")}
    resp = await client.post(
        f"/orders/{order_id}/deliver-attachments",
        files=data,
        headers=auth_header(booster),
    )
    assert resp.status_code == 400
    assert "已取消" in resp.json()["detail"]


async def test_cancel_keeps_settled_claims(client: AsyncClient, admin_user: dict):
    # 2 名额只结算 1 个：订单保持 LOCKED 可取消；已结算名额不受取消影响
    order = await _create_order(client, admin_user, max_claims=2)
    order_id = order["id"]
    booster = await _register(client, "cancel-b@example.com", "CancelBoosterB")

    resp = await client.put(f"/orders/{order_id}/accept", headers=auth_header(booster))
    assert resp.status_code == 200
    resp = await client.put(
        f"/orders/{order_id}/deliver",
        json={"delivery_note": "完成"},
        headers=auth_header(booster),
    )
    assert resp.status_code == 200

    resp = await client.get(f"/orders/{order_id}/claims", headers=auth_header(admin_user))
    claim_id = resp.json()["items"][0]["id"]
    resp = await client.put(
        f"/orders/{order_id}/claims/{claim_id}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "SETTLED"

    # 取消已结算名额所在的订单：SETTLED 名额保留，不被改成 CANCELLED
    resp = await client.put(f"/orders/{order_id}/cancel", headers=auth_header(admin_user))
    assert resp.status_code == 200

    resp = await client.get(
        "/orders/claims/mine?status=SETTLED", headers=auth_header(booster)
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    resp = await client.get(
        "/orders/claims/mine?status=CANCELLED", headers=auth_header(booster)
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
