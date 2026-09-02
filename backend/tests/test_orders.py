"""Order lifecycle and payment tests."""

from httpx import AsyncClient
from tests.conftest import auth_header


async def _create_order(client: AsyncClient, user_data: dict) -> dict:
    """Helper: create a standard test order."""
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "500.00",
            "description_raw": "钻石上王者",
        },
        headers=auth_header(user_data),
    )
    assert resp.status_code == 201
    return resp.json()


async def test_create_order(client: AsyncClient, admin_user: dict):
    order = await _create_order(client, admin_user)
    assert order["status"] == "PENDING"
    assert order["game_name"] == "王者荣耀"
    assert order["payment_status"] == "UNPAID"


async def test_create_order_no_auth(client: AsyncClient):
    resp = await client.post("/orders/create", json={
        "game_name": "王者荣耀",
        "current_rank": "钻石",
        "target_rank": "王者",
        "price": "500.00",
    })
    assert resp.status_code == 401


async def test_accept_order(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    order = await _create_order(client, admin_user)
    resp = await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(booster_user),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "LOCKED"


async def test_accept_order_non_booster(
    client: AsyncClient, registered_user: dict, admin_user: dict
):
    """新权限模型：注册用户（USER 角色）无需打手身份即可抢单；管理员不能接单。"""
    order = await _create_order(client, admin_user)

    # Register a second regular user
    resp = await client.post("/auth/register", json={
        "email": "regular2@example.com",
        "username": "Regular2",
        "password": "RegularPass1",
    })
    regular = resp.json()

    # 普通用户可直接抢单（人人皆打手）
    resp = await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(regular),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "LOCKED"


async def test_accept_order_rejects_admin(
    client: AsyncClient, admin_user: dict
):
    """管理员（老板）不能作为打手接自己的单：403。"""
    order = await _create_order(client, admin_user)

    resp = await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 403


async def test_deliver_order(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """Booster delivers order -> status becomes DELIVERED."""
    order = await _create_order(client, admin_user)
    await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(booster_user),
    )
    resp = await client.put(
        f"/orders/{order['id']}/deliver",
        headers=auth_header(booster_user),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "DELIVERED"


async def test_confirm_order(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """Boss confirms delivered order -> status becomes COMPLETED."""
    order = await _create_order(client, admin_user)
    await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(booster_user),
    )
    await client.put(
        f"/orders/{order['id']}/deliver",
        headers=auth_header(booster_user),
    )
    resp = await client.put(
        f"/orders/{order['id']}/confirm",
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "COMPLETED"


async def test_pay_order(client: AsyncClient, admin_user: dict):
    order = await _create_order(client, admin_user)
    resp = await client.put(
        f"/orders/{order['id']}/pay",
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["payment_status"] == "PAID"
    assert data["paid_at"] is not None


# ---------------------------------------------------------------------------
# 管理员（老板）发布订单：进入公共大厅供打手抢单
# ---------------------------------------------------------------------------


async def test_admin_create_order(client: AsyncClient, admin_user: dict):
    """管理员可直接发布订单：201、PENDING、无打手（进入公共大厅）。"""
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "三角洲行动",
            "current_rank": "黄金",
            "target_rank": "钻石",
            "price": "300.00",
            "description_raw": "老板发布的三角洲派单",
        },
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "PENDING"
    assert data["booster_id"] is None
    assert data["game_name"] == "三角洲行动"


async def test_admin_order_visible_to_booster_and_acceptable(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """管理员发布的 PENDING 订单出现在打手大厅列表并可被接单。"""
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "三角洲行动",
            "current_rank": "黄金",
            "target_rank": "钻石",
            "price": "300.00",
        },
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201
    order = resp.json()

    # 打手订单列表（大厅）能看到该 PENDING 订单
    resp = await client.get("/orders/", headers=auth_header(booster_user))
    assert resp.status_code == 200
    ids = [o["id"] for o in resp.json()["items"]]
    assert order["id"] in ids

    # 打手接单成功
    resp = await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(booster_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "LOCKED"
    assert data["booster_id"] is not None


async def test_booster_cannot_create_order(client: AsyncClient, booster_user: dict):
    """代练不允许发单：403。"""
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "三角洲行动",
            "current_rank": "黄金",
            "target_rank": "钻石",
            "price": "300.00",
        },
        headers=auth_header(booster_user),
    )
    assert resp.status_code == 403


async def test_user_cannot_create_order(client: AsyncClient, registered_user: dict):
    """发单已收敛为管理员（老板）专属：普通用户下单返回 403。"""
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "500.00",
        },
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 403


async def test_admin_create_order_notifies_boosters(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session,
):
    """管理员发单后，活跃打手收到"新订单"系统通知（一次事务批量写入）。"""
    from sqlalchemy import select

    from app.models.notification import Notification, NotificationType
    from app.models.user import User

    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "三角洲行动",
            "current_rank": "黄金",
            "target_rank": "钻石",
            "price": "300.00",
        },
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201
    order = resp.json()

    booster_result = await db_session.execute(
        select(User).where(User.email == "booster@example.com")
    )
    booster = booster_result.scalar_one()
    notif_result = await db_session.execute(
        select(Notification).where(
            Notification.user_id == booster.id,
            Notification.type == NotificationType.SYSTEM_ANNOUNCEMENT,
        )
    )
    notifications = list(notif_result.scalars().all())
    assert len(notifications) >= 1
    assert any(n.ref_id == order["id"] for n in notifications)
    assert any("新订单" in n.title for n in notifications)
