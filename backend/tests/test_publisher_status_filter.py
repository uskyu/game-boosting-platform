"""派单（我的发布）状态下「进行中/待确认」筛选口径测试。

名额制交付只把 claim 推到 DELIVERED（OrderStatus.DELIVERED 全仓无人写入），
订单整体保持 LOCKED。因此 mine_published 作用域下：

- status=DELIVERED（待确认）应命中「LOCKED + 存在 DELIVERED 状态 claim」的订单，
  以及订单本身就是 DELIVERED 的管理员干预遗留单；
- status=LOCKED（进行中）应命中「LOCKED 且不存在 DELIVERED 状态 claim」的订单；
- hall 作用域（非 mine_published）保持字面状态匹配不变。
"""

from httpx import AsyncClient
from sqlalchemy import select

from app.models.order import Order, OrderStatus
from tests.conftest import auth_header
from tests.test_escrow import _adjust_balance, _register


async def _create_order(client: AsyncClient, user: dict) -> dict:
    """Publisher's own order (escrow 需余额，调用方先充值)。"""
    resp = await client.post(
        "/orders/create",
        json={"game_name": "王者荣耀", "price": "100.00", "max_claims": 1},
        headers=auth_header(user),
    )
    assert resp.status_code == 201
    return resp.json()


async def _accept_and_deliver(client: AsyncClient, order_id: int, booster: dict) -> None:
    resp = await client.put(f"/orders/{order_id}/accept", headers=auth_header(booster))
    assert resp.status_code == 200
    resp = await client.put(
        f"/orders/{order_id}/deliver",
        json={"delivery_note": "已完成"},
        headers=auth_header(booster),
    )
    assert resp.status_code == 200


async def _list(client: AsyncClient, user: dict, **params) -> dict:
    resp = await client.get("/orders/", params=params, headers=auth_header(user))
    assert resp.status_code == 200
    return resp.json()


def _ids(data: dict) -> set[int]:
    return {item["id"] for item in data["items"]}


async def _publisher_with_balance(client: AsyncClient, admin_user: dict, tag: str):
    publisher = await _register(client, f"pub.{tag}@example.com", f"Pub{tag}")
    await _adjust_balance(client, admin_user, publisher, "1000.00")
    return publisher


async def test_mine_published_delivered_matches_locked_order_with_pending_review(
    client: AsyncClient, admin_user: dict
):
    """有待审核 claim 的 LOCKED 单应命中 mine_published 的「待确认」筛选。"""
    publisher = await _publisher_with_balance(client, admin_user, "delivered")
    booster = await _register(client, "pub.delivered.booster@example.com", "DelivBooster")

    order = await _create_order(client, publisher)
    await _accept_and_deliver(client, order["id"], booster)
    # 交付后订单整体仍是 LOCKED，但有待审核交付
    assert order["status"] == "PENDING"

    data = await _list(client, publisher, mine_published="true", status="DELIVERED", page_size=100)
    assert data["total"] == 1
    assert _ids(data) == {order["id"]}


async def test_mine_published_locked_excludes_pending_review(
    client: AsyncClient, admin_user: dict
):
    """同一张有待审核 claim 的 LOCKED 单不应命中 mine_published 的「进行中」筛选。"""
    publisher = await _publisher_with_balance(client, admin_user, "lockedout")
    booster = await _register(client, "pub.lockedout.booster@example.com", "LockOutBooster")

    order = await _create_order(client, publisher)
    await _accept_and_deliver(client, order["id"], booster)

    data = await _list(client, publisher, mine_published="true", status="LOCKED", page_size=100)
    assert data["total"] == 0
    assert _ids(data) == set()


async def test_mine_published_locked_matches_order_without_pending_review(
    client: AsyncClient, admin_user: dict
):
    """无待审核 claim 的 LOCKED 单命中「进行中」、不命中「待确认」。"""
    publisher = await _publisher_with_balance(client, admin_user, "lockedin")
    booster = await _register(client, "pub.lockedin.booster@example.com", "LockInBooster")

    order = await _create_order(client, publisher)
    resp = await client.put(f"/orders/{order['id']}/accept", headers=auth_header(booster))
    assert resp.status_code == 200
    assert resp.json()["status"] == "LOCKED"

    data = await _list(client, publisher, mine_published="true", status="LOCKED", page_size=100)
    assert data["total"] == 1
    assert _ids(data) == {order["id"]}

    data = await _list(client, publisher, mine_published="true", status="DELIVERED", page_size=100)
    assert data["total"] == 0
    assert _ids(data) == set()


async def test_mine_published_delivered_matches_legacy_delivered_order(
    client: AsyncClient, admin_user: dict, db_session
):
    """订单本身就是 DELIVERED 的管理员干预遗留单同时命中「待确认」、不命中「进行中」。"""
    publisher = await _publisher_with_balance(client, admin_user, "legacy")

    order = await _create_order(client, publisher)

    # 直接改库模拟管理员干预的遗留 DELIVERED 订单
    result = await db_session.execute(select(Order).where(Order.id == order["id"]))
    order_row = result.scalar_one()
    order_row.status = OrderStatus.DELIVERED
    await db_session.commit()

    data = await _list(client, publisher, mine_published="true", status="DELIVERED", page_size=100)
    assert data["total"] == 1
    assert _ids(data) == {order["id"]}

    data = await _list(client, publisher, mine_published="true", status="LOCKED", page_size=100)
    assert data["total"] == 0
    assert _ids(data) == set()


async def test_hall_scope_status_filter_is_literal(
    client: AsyncClient, admin_user: dict, db_session
):
    """hall 作用域（非 mine_published）保持字面状态匹配：有待审核 claim 的
    LOCKED 单不会被当成 DELIVERED，遗留 DELIVERED 单也不会被当成 LOCKED。"""
    publisher = await _publisher_with_balance(client, admin_user, "hall")
    booster = await _register(client, "pub.hall.booster@example.com", "HallBooster")

    pending_review_order = await _create_order(client, publisher)
    await _accept_and_deliver(client, pending_review_order["id"], booster)

    legacy_delivered = await _create_order(client, publisher)
    result = await db_session.execute(select(Order).where(Order.id == legacy_delivered["id"]))
    legacy_row = result.scalar_one()
    legacy_row.status = OrderStatus.DELIVERED
    await db_session.commit()

    # 发布人不带 mine_published 拉自己的单：字面匹配
    data = await _list(client, publisher, status="LOCKED", page_size=100)
    assert data["total"] == 1
    assert _ids(data) == {pending_review_order["id"]}

    data = await _list(client, publisher, status="DELIVERED", page_size=100)
    assert data["total"] == 1
    assert _ids(data) == {legacy_delivered["id"]}
