"""北京时间自然日边界：后台订单趋势与大厅今日已接单保持一致。"""
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.product_time import PRODUCT_TIMEZONE, product_day_bounds, product_local_date
from app.models.order import ClaimLifecycleStatus, ClaimStatus, Order, OrderClaim, OrderStatus
from app.services.dashboard_service import DashboardService
from app.services.recent_claims_service import RecentClaimsService


def _beijing_today_start_utc_naive() -> datetime:
    today = product_local_date(datetime.now(timezone.utc))
    local_midnight = datetime.combine(today, time.min, tzinfo=PRODUCT_TIMEZONE)
    return product_day_bounds(local_midnight)[0]


async def _add_order_claim(
    db: AsyncSession,
    *,
    publisher_id: int,
    booster_id: int,
    created_at: datetime,
    price: str,
    title: str,
) -> OrderClaim:
    order = Order(
        user_id=publisher_id,
        booster_id=booster_id,
        game_name="王者荣耀",
        current_rank="钻石",
        target_rank="王者",
        title=title,
        description_raw="timezone boundary test",
        price=Decimal(price),
        status=OrderStatus.LOCKED,
        claim_status=ClaimStatus.FULL,
        max_claims=1,
        claimed_count=1,
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(order)
    await db.flush()
    claim = OrderClaim(
        order_id=order.id,
        booster_id=booster_id,
        status=ClaimLifecycleStatus.CLAIMED,
        created_at=created_at,
    )
    db.add(claim)
    await db.flush()
    return claim


@pytest.mark.asyncio
async def test_order_trend_day_starts_at_beijing_midnight(
    db_session: AsyncSession, registered_user: dict, booster_user: dict
):
    """UTC 前一日 16:00（北京午夜）前一秒排除，午夜整计入当天趋势。"""
    start_utc = _beijing_today_start_utc_naive()
    publisher_id = registered_user["user"]["id"]
    booster_id = booster_user["user"]["id"]
    await _add_order_claim(
        db_session,
        publisher_id=publisher_id,
        booster_id=booster_id,
        created_at=start_utc - timedelta(seconds=1),
        price="10.00",
        title="昨天最后一秒",
    )
    await _add_order_claim(
        db_session,
        publisher_id=publisher_id,
        booster_id=booster_id,
        created_at=start_utc,
        price="20.00",
        title="今天零点整",
    )
    await db_session.flush()

    result = await DashboardService(db_session).get_order_trend(period="day", days=0)
    today_label = product_local_date(datetime.now(timezone.utc)).isoformat()
    today = next((point for point in result.points if point.date == today_label), None)
    assert today is not None
    assert today.count == 1
    assert today.revenue == 20.0


@pytest.mark.asyncio
async def test_recent_claims_uses_the_same_beijing_midnight_boundary(
    db_session: AsyncSession, registered_user: dict, booster_user: dict
):
    """大厅今日已接单与后台趋势使用同一北京时间自然日范围。"""
    start_utc = _beijing_today_start_utc_naive()
    publisher_id = registered_user["user"]["id"]
    booster_id = booster_user["user"]["id"]
    yesterday_claim = await _add_order_claim(
        db_session,
        publisher_id=publisher_id,
        booster_id=booster_id,
        created_at=start_utc - timedelta(seconds=1),
        price="10.00",
        title="昨天最后一秒",
    )
    today_claim = await _add_order_claim(
        db_session,
        publisher_id=publisher_id,
        booster_id=booster_id,
        created_at=start_utc,
        price="20.00",
        title="今天零点整",
    )
    await db_session.flush()

    items, total = await RecentClaimsService(db_session).list_recent_claims()
    assert total == 1
    assert [item["id"] for item in items] == [today_claim.id]
    assert yesterday_claim.id not in [item["id"] for item in items]
