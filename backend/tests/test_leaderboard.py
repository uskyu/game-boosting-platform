"""排行榜：按接单金额、取消扣除、前 30 与我的排名差额。"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import ClaimLifecycleStatus, Order, OrderClaim, OrderStatus
from app.models.user import User, UserRole
from app.services.leaderboard_service import get_leaderboard_service
from tests.conftest import auth_header


def _order(customer_id: int, booster_id: int, price: str, title: str) -> Order:
    return Order(
        user_id=customer_id,
        booster_id=booster_id,
        game_name="三角洲行动",
        current_rank="黄金",
        target_rank="钻石",
        title=title,
        price=Decimal(price),
        status=OrderStatus.LOCKED,
        max_claims=1,
        claimed_count=1,
    )


async def test_leaderboard_ranks_by_claimed_amount_and_gap(
    db_session: AsyncSession, registered_user: dict, booster_user: dict
):
    customer_id = registered_user["user"]["id"]
    booster1_id = booster_user["user"]["id"]

    booster2 = User(
        email="booster2@example.com",
        username="第二打手",
        hashed_password="x",
        role=UserRole.BOOSTER,
    )
    db_session.add(booster2)
    await db_session.flush()
    booster2_id = booster2.id

    orders = [
        _order(customer_id, booster1_id, "100.00", "单1"),
        _order(customer_id, booster1_id, "50.00", "单2"),
        _order(customer_id, booster2_id, "80.00", "单3"),
    ]
    cancelled = _order(customer_id, booster2_id, "999.00", "取消单")
    cancelled.status = OrderStatus.CANCELLED
    orders.append(cancelled)
    db_session.add_all(orders)
    await db_session.flush()

    claims = [
        OrderClaim(order_id=orders[0].id, booster_id=booster1_id),
        OrderClaim(order_id=orders[1].id, booster_id=booster1_id),
        OrderClaim(order_id=orders[2].id, booster_id=booster2_id),
        OrderClaim(
            order_id=orders[3].id,
            booster_id=booster2_id,
            status=ClaimLifecycleStatus.CANCELLED,
        ),
    ]
    db_session.add_all(claims)
    await db_session.commit()

    booster1 = (
        await db_session.execute(select(User).where(User.id == booster1_id))
    ).scalar_one()

    service = get_leaderboard_service(db_session)
    board = await service.get_leaderboard(booster1, "day")
    assert [item.username for item in board.items] == ["TestBooster", "第二打手"]
    assert [item.amount for item in board.items] == [150.0, 80.0]
    assert board.me.rank == 1
    assert board.me.amount == 150.0
    assert board.me.gap_to_previous is None

    booster2 = (
        await db_session.execute(select(User).where(User.id == booster2_id))
    ).scalar_one()
    board2 = await service.get_leaderboard(booster2, "day")
    assert board2.me.rank == 2
    assert board2.me.amount == 80.0
    assert board2.me.gap_to_previous == 70.0

    # 未接单用户不上榜
    customer = (
        await db_session.execute(select(User).where(User.id == customer_id))
    ).scalar_one()
    board3 = await service.get_leaderboard(customer, "day")
    assert board3.me.rank is None
    assert board3.me.amount == 0.0


async def test_leaderboard_cancelled_claim_is_deducted(
    db_session: AsyncSession, registered_user: dict, booster_user: dict
):
    customer_id = registered_user["user"]["id"]
    booster_id = booster_user["user"]["id"]
    order = _order(customer_id, booster_id, "60.00", "会取消的单")
    db_session.add(order)
    await db_session.flush()
    claim = OrderClaim(
        order_id=order.id,
        booster_id=booster_id,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(claim)
    await db_session.commit()

    booster = (
        await db_session.execute(select(User).where(User.id == booster_id))
    ).scalar_one()
    service = get_leaderboard_service(db_session)

    board = await service.get_leaderboard(booster, "day")
    assert board.me.amount == 60.0

    claim.status = ClaimLifecycleStatus.CANCELLED
    order.status = OrderStatus.CANCELLED
    await db_session.commit()

    board = await service.get_leaderboard(booster, "day")
    assert board.items == []
    assert board.me.rank is None
    assert board.me.amount == 0.0


async def test_leaderboard_api_and_admin_completed_count(
    client,
    db_session: AsyncSession,
    registered_user: dict,
    booster_user: dict,
    admin_user: dict,
):
    """接口层验证：榜单可访问，后台排行榜「完成」不再恒为 0。"""
    customer_id = registered_user["user"]["id"]
    booster_id = booster_user["user"]["id"]
    order = _order(customer_id, booster_id, "120.00", "已完成单")
    order.status = OrderStatus.COMPLETED
    db_session.add(order)
    await db_session.flush()
    db_session.add(
        OrderClaim(
            order_id=order.id,
            booster_id=booster_id,
            status=ClaimLifecycleStatus.SETTLED,
        )
    )
    await db_session.commit()

    resp = await client.get(
        "/leaderboard", params={"period": "day"}, headers=auth_header(booster_user)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"][0]["user_id"] == booster_id
    assert body["items"][0]["amount"] == 120.0

    resp = await client.get(
        "/admin/dashboard/booster-ranking",
        params={"limit": 10},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    item = next(row for row in resp.json()["items"] if row["user_id"] == booster_id)
    assert item["total_completed"] == 1
    assert item["total_revenue"] == 120.0
