"""用户发单/接单管控测试：can_publish / can_accept 与 is_active 解耦。"""

from httpx import AsyncClient

from tests.conftest import auth_header


async def _create_order(client: AsyncClient, user_data: dict, price: str = "500.00") -> dict:
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": price,
            "description_raw": "钻石上王者",
        },
        headers=auth_header(user_data),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _topup(client: AsyncClient, admin_user: dict, user: dict, amount: str = "1000.00") -> None:
    resp = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": amount, "reason": "测试充值"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text


async def _restrict(client: AsyncClient, admin_user: dict, user_id: int, payload: dict):
    resp = await client.post(
        f"/admin/users/{user_id}/restrictions",
        json=payload,
        headers=auth_header(admin_user),
    )
    return resp


async def test_banned_publish_forbidden_then_recover(
    client: AsyncClient, registered_user: dict, admin_user: dict
):
    """禁发单后 POST /orders/create → 403；解禁后恢复 201。"""
    await _topup(client, admin_user, registered_user)
    user_id = registered_user["user"]["id"]

    # 解禁状态可发单（冒烟）
    await _create_order(client, registered_user, price="10.00")

    resp = await _restrict(client, admin_user, user_id, {"can_publish": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["can_publish"] is False
    assert resp.json()["can_accept"] is True

    resp = await client.post(
        "/orders/create",
        json={"game_name": "王者荣耀", "price": "10.00"},
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 403
    assert "禁止发布" in resp.json()["detail"]

    resp = await _restrict(client, admin_user, user_id, {"can_publish": True})
    assert resp.status_code == 200
    assert resp.json()["can_publish"] is True

    await _create_order(client, registered_user, price="10.00")


async def test_banned_accept_forbidden(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """禁接单后 PUT /orders/{id}/accept → 403。"""
    order = await _create_order(client, admin_user)
    booster_id = booster_user["user"]["id"]

    resp = await _restrict(client, admin_user, booster_id, {"can_accept": False})
    assert resp.status_code == 200
    assert resp.json()["can_accept"] is False

    resp = await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(booster_user),
    )
    assert resp.status_code == 403
    assert "禁止接单" in resp.json()["detail"]

    # 解禁后可正常接单
    resp = await _restrict(client, admin_user, booster_id, {"can_accept": True})
    assert resp.status_code == 200
    resp = await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(booster_user),
    )
    assert resp.status_code == 200


async def test_assign_to_banned_user_rejected(
    client: AsyncClient, registered_user: dict, admin_user: dict
):
    """派单给禁接用户 → 4xx。"""
    order = await _create_order(client, admin_user)
    user_id = registered_user["user"]["id"]

    resp = await _restrict(client, admin_user, user_id, {"can_accept": False})
    assert resp.status_code == 200

    resp = await client.put(
        f"/admin/orders/{order['id']}/assign",
        json={"booster_id": user_id},
        headers=auth_header(admin_user),
    )
    assert resp.status_code in (400, 403)
    assert "禁止接单" in resp.json()["detail"]


async def test_cannot_restrict_self(client: AsyncClient, admin_user: dict):
    """不能给自己设置管控限制 → 400。"""
    admin_id = admin_user["user"]["id"]
    resp = await _restrict(client, admin_user, admin_id, {"can_publish": False})
    assert resp.status_code == 400

    resp = await _restrict(client, admin_user, admin_id, {"can_accept": False})
    assert resp.status_code == 400


async def test_restriction_fields_visible_in_responses(
    client: AsyncClient, registered_user: dict, admin_user: dict
):
    """列表/详情回显 can_publish / can_accept。"""
    user_id = registered_user["user"]["id"]
    resp = await client.get(f"/admin/users/{user_id}", headers=auth_header(admin_user))
    assert resp.status_code == 200
    assert resp.json()["can_publish"] is True
    assert resp.json()["can_accept"] is True

    resp = await client.get("/admin/users", headers=auth_header(admin_user))
    assert resp.status_code == 200
    assert all("can_publish" in item and "can_accept" in item for item in resp.json()["items"])
