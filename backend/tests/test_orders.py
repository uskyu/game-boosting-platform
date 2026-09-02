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
    """Booster delivers their claim -> claim becomes DELIVERED, order stays LOCKED."""
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
    data = resp.json()
    # Claim-level delivery: the order itself keeps running (LOCKED)
    assert data["status"] == "LOCKED"
    # The caller's claim is attached and marked DELIVERED
    assert data["my_claim"] is not None
    assert data["my_claim"]["status"] == "DELIVERED"
    assert data["my_claim"]["delivered_at"] is not None
    assert data["my_claim"]["booster_id"] == booster_user["user"]["id"]

    # claims/mine lists the delivered claim
    resp = await client.get(
        "/orders/claims/mine",
        headers=auth_header(booster_user),
    )
    assert resp.status_code == 200
    mine = resp.json()
    assert mine["total"] == 1
    assert mine["items"][0]["status"] == "DELIVERED"
    assert mine["items"][0]["order"]["id"] == order["id"]
    assert mine["items"][0]["order"]["status"] == "LOCKED"


async def test_deliver_requires_claimed_user(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    """A user without a claim on the order cannot deliver (403)."""
    order = await _create_order(client, admin_user)
    resp = await client.put(
        f"/orders/{order['id']}/deliver",
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 403
    assert "只有已报名的打手才能交付" in resp.json()["detail"]


async def test_confirm_order(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """Boss confirms delivered claim -> 1/1 settled, order becomes COMPLETED."""
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
    data = resp.json()
    assert data["status"] == "COMPLETED"
    assert data["completed_at"] is not None

    # The booster's claim is settled
    resp = await client.get(
        "/orders/claims/mine?status=SETTLED",
        headers=auth_header(booster_user),
    )
    assert resp.status_code == 200
    settled = [i for i in resp.json()["items"] if i["order"]["id"] == order["id"]]
    assert len(settled) == 1
    assert settled[0]["settled_at"] is not None


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
    """人人可发单模式：代练也可发单，但余额须覆盖托管金额，无余额返回 400。"""
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "三角洲行动",
            "price": "300.00",
        },
        headers=auth_header(booster_user),
    )
    assert resp.status_code == 400
    assert "余额不足" in resp.json()["detail"]


async def test_user_cannot_create_order(client: AsyncClient, registered_user: dict):
    """人人可发单模式：普通用户可发单，但无余额时返回 400 而非 403。"""
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "price": "500.00",
        },
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 400
    assert "余额不足" in resp.json()["detail"]


async def test_mine_published_filters_to_regular_users_own_orders(
    client: AsyncClient, admin_user: dict
):
    """mine_published excludes hall orders and orders published by other users."""
    from tests.test_escrow import _adjust_balance, _register

    publisher = await _register(client, "mine.pub@example.com", "MinePublisher")
    other = await _register(client, "mine.other@example.com", "OtherPublisher")
    await _adjust_balance(client, admin_user, publisher, "1000.00")
    await _adjust_balance(client, admin_user, other, "1000.00")

    own = await _create_order(client, publisher)
    other_order = await client.post(
        "/orders/create",
        json={"game_name": "王者荣耀", "price": "100.00"},
        headers=auth_header(other),
    )
    assert other_order.status_code == 201
    hall = await _create_order(client, admin_user)

    resp = await client.get(
        "/orders/?mine_published=true&page=1&page_size=100",
        headers=auth_header(publisher),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert [item["id"] for item in data["items"]] == [own["id"]]
    assert other_order.json()["id"] != own["id"]
    assert hall["id"] not in [item["id"] for item in data["items"]]


async def test_mine_published_filters_to_admin_own_orders(
    client: AsyncClient, admin_user: dict, db_session
):
    """Admins using mine_published also see only orders they published."""
    from sqlalchemy import select
    from app.models.user import User, UserRole

    other = await client.post(
        "/auth/register",
        json={"email": "mine.admin.other@example.com", "username": "Other", "password": "Pass12345"},
    )
    assert other.status_code in (200, 201)
    result = await db_session.execute(
        select(User).where(User.email == "mine.admin.other@example.com")
    )
    other_user = result.scalar_one()
    other_user.role = UserRole.ADMIN
    await db_session.commit()
    other_login = await client.post(
        "/auth/login",
        json={"email": "mine.admin.other@example.com", "password": "Pass12345"},
    )
    assert other_login.status_code == 200
    other = other_login.json()

    own = await _create_order(client, admin_user)
    other_order = await _create_order(client, other)

    resp = await client.get(
        "/orders/?mine_published=true&page=1&page_size=100",
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()["items"]]
    assert ids == [own["id"]]
    assert other_order["id"] not in ids


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
