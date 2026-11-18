"""管理员发单数据：产品时区事件统计与可追溯明细。"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.product_time import PRODUCT_TIMEZONE
from app.models.order import ClaimLifecycleStatus, Order, OrderClaim, OrderStatus
from tests.conftest import auth_header


def _beijing_time(day: date, hour: int = 12) -> datetime:
    """Create a naive UTC database timestamp at a Beijing wall-clock hour."""
    local = datetime.combine(day, time(hour, 0), tzinfo=PRODUCT_TIMEZONE)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


async def _make_order(
    db_session: AsyncSession,
    *,
    publisher_id: int,
    title: str,
    price: str,
    created_at: datetime,
    status: OrderStatus = OrderStatus.PENDING,
    max_claims: int = 1,
    completed_at: datetime | None = None,
    cancelled_at: datetime | None = None,
) -> Order:
    order = Order(
        user_id=publisher_id,
        game_name="三角洲行动",
        current_rank="黄金",
        target_rank="钻石",
        title=title,
        price=Decimal(price),
        max_claims=max_claims,
        claimed_count=max_claims if status == OrderStatus.COMPLETED else 0,
        status=status,
        created_at=created_at,
        updated_at=created_at,
        completed_at=completed_at,
        cancelled_at=cancelled_at,
    )
    db_session.add(order)
    await db_session.flush()
    return order


async def test_order_analytics_uses_beijing_event_dates_and_settled_slots(
    client: AsyncClient,
    db_session: AsyncSession,
    registered_user: dict,
    booster_user: dict,
    admin_user: dict,
):
    today = datetime.now(timezone.utc).astimezone(PRODUCT_TIMEZONE).date()
    publisher_id = registered_user["user"]["id"]
    booster_id = booster_user["user"]["id"]
    second_booster_id = admin_user["user"]["id"]
    today_at = _beijing_time(today, 12)

    # A current issuance, but it is not yet completed or cancelled.
    issued = await _make_order(
        db_session,
        publisher_id=publisher_id,
        title="今日新发订单",
        price="100.00",
        created_at=today_at,
        max_claims=2,
    )

    # The order was issued outside the last seven days, but cancelled today.
    cancelled = await _make_order(
        db_session,
        publisher_id=publisher_id,
        title="今日取消订单",
        price="70.00",
        created_at=_beijing_time(today - timedelta(days=8)),
        status=OrderStatus.CANCELLED,
        cancelled_at=today_at,
    )

    # Completion and settlement are event-based, independent of issue date.
    completed = await _make_order(
        db_session,
        publisher_id=publisher_id,
        title="多名额已完结订单",
        price="250.00",
        created_at=_beijing_time(today - timedelta(days=40)),
        status=OrderStatus.COMPLETED,
        max_claims=2,
        completed_at=today_at,
    )
    db_session.add_all(
        [
            OrderClaim(
                order_id=completed.id,
                booster_id=booster_id,
                status=ClaimLifecycleStatus.SETTLED,
                settled_at=today_at,
                created_at=completed.created_at,
            ),
            OrderClaim(
                order_id=completed.id,
                booster_id=second_booster_id,
                status=ClaimLifecycleStatus.SETTLED,
                settled_at=today_at,
                created_at=completed.created_at,
            ),
        ]
    )

    # Legacy completed one-slot orders without order_claims count once.
    legacy = await _make_order(
        db_session,
        publisher_id=publisher_id,
        title="历史单名额订单",
        price="120.00",
        created_at=_beijing_time(today - timedelta(days=60)),
        status=OrderStatus.COMPLETED,
        completed_at=today_at,
    )
    await db_session.commit()

    response = await client.get(
        "/admin/order-analytics?period=7d",
        headers=auth_header(admin_user),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["timezone"] == "Asia/Shanghai"
    assert data["period_start"] == (today - timedelta(days=6)).isoformat()
    assert data["period_end"] == today.isoformat()
    assert data["summary"] == {
        "period_start": (today - timedelta(days=6)).isoformat(),
        "period_end": today.isoformat(),
        "issued_orders": 1,
        "cancelled_orders": 1,
        "completed_orders": 2,
        "settled_slots": 3,
        "issued_amount": 200.0,
        "successful_amount": 620.0,
    }
    today_row = next(row for row in data["daily"] if row["date"] == today.isoformat())
    assert today_row["issued_orders"] == 1
    assert today_row["cancelled_orders"] == 1
    assert today_row["completed_orders"] == 2
    assert today_row["settled_slots"] == 3
    assert today_row["issued_amount"] == 200.0
    assert today_row["successful_amount"] == 620.0

    issued_amount_details = await client.get(
        "/admin/order-analytics/details",
        params={
            "period": "7d",
            "date": today.isoformat(),
            "metric": "issued_amount",
        },
        headers=auth_header(admin_user),
    )
    assert issued_amount_details.status_code == 200, issued_amount_details.text
    assert [item["order_id"] for item in issued_amount_details.json()["items"]] == [issued.id]

    settled_details = await client.get(
        "/admin/order-analytics/details",
        params={"period": "7d", "date": today.isoformat(), "metric": "settled"},
        headers=auth_header(admin_user),
    )
    assert settled_details.status_code == 200, settled_details.text
    settled_data = settled_details.json()
    assert settled_data["total"] == 2
    assert settled_data["pages"] == 1
    detail_by_id = {item["order_id"]: item for item in settled_data["items"]}
    assert detail_by_id[completed.id]["settled_slots"] == 2
    assert detail_by_id[completed.id]["amount"] == 500.0
    assert detail_by_id[legacy.id]["settled_slots"] == 1
    assert detail_by_id[legacy.id]["amount"] == 120.0

    cancelled_details = await client.get(
        "/admin/order-analytics/details",
        params={"period": "7d", "date": today.isoformat(), "metric": "cancelled"},
        headers=auth_header(admin_user),
    )
    assert cancelled_details.status_code == 200, cancelled_details.text
    assert [item["order_id"] for item in cancelled_details.json()["items"]] == [cancelled.id]


async def test_publisher_analytics_is_self_scoped_and_lists_publisher_name(
    client: AsyncClient,
    db_session: AsyncSession,
    registered_user: dict,
    booster_user: dict,
):
    today = datetime.now(timezone.utc).astimezone(PRODUCT_TIMEZONE).date()
    today_at = _beijing_time(today)
    publisher_id = registered_user["user"]["id"]
    other_publisher_id = booster_user["user"]["id"]

    active = await _make_order(
        db_session,
        publisher_id=publisher_id,
        title="我的有效订单",
        price="50.00",
        created_at=today_at,
        max_claims=2,
    )
    cancelled = await _make_order(
        db_session,
        publisher_id=publisher_id,
        title="我的取消订单",
        price="900.00",
        created_at=today_at,
        status=OrderStatus.CANCELLED,
        cancelled_at=today_at,
    )
    other_order = await _make_order(
        db_session,
        publisher_id=other_publisher_id,
        title="其他账号订单",
        price="700.00",
        created_at=today_at,
        max_claims=1,
    )
    await db_session.commit()

    response = await client.get(
        f"/orders/my-analytics?period=today&user_id={other_publisher_id}",
        headers=auth_header(registered_user),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["summary"]["issued_orders"] == 2
    assert data["summary"]["cancelled_orders"] == 1
    assert data["summary"]["issued_amount"] == 100.0
    assert data["summary"]["successful_amount"] == 0.0
    assert data["lifetime"] == {
        "issued_orders": 2,
        "cancelled_orders": 1,
        "completed_orders": 0,
        "settled_slots": 0,
        "issued_amount": 100.0,
        "successful_amount": 0.0,
    }

    details = await client.get(
        "/orders/my-analytics/details",
        params={
            "period": "today",
            "date": today.isoformat(),
            "metric": "issued",
            "user_id": other_publisher_id,
        },
        headers=auth_header(registered_user),
    )
    assert details.status_code == 200, details.text
    details_data = details.json()
    assert details_data["total"] == 2
    detail_by_id = {item["order_id"]: item for item in details_data["items"]}
    assert set(detail_by_id) == {active.id, cancelled.id}
    assert detail_by_id[active.id]["metric_amount"] == 100.0
    assert detail_by_id[cancelled.id]["metric_amount"] == 900.0

    amount_details = await client.get(
        "/orders/my-analytics/details",
        params={
            "period": "today",
            "date": today.isoformat(),
            "metric": "issued_amount",
        },
        headers=auth_header(registered_user),
    )
    assert amount_details.status_code == 200, amount_details.text
    assert amount_details.json()["total"] == 1
    assert amount_details.json()["items"][0]["order_id"] == active.id

    order_list = await client.get("/orders/?mine_published=true", headers=auth_header(registered_user))
    assert order_list.status_code == 200, order_list.text
    listed = {item["id"]: item for item in order_list.json()["items"]}
    assert active.id in listed
    assert listed[active.id]["publisher_nickname"] == registered_user["user"]["username"]
    assert other_order.id not in listed


async def test_admin_intervention_sets_order_cancelled_at(
    client: AsyncClient,
    db_session: AsyncSession,
    registered_user: dict,
    admin_user: dict,
):
    today_at = _beijing_time(datetime.now(timezone.utc).astimezone(PRODUCT_TIMEZONE).date())
    order = await _make_order(
        db_session,
        publisher_id=registered_user["user"]["id"],
        title="后台取消时间戳测试",
        price="50.00",
        created_at=today_at,
    )
    await db_session.commit()

    response = await client.put(
        f"/admin/orders/{order.id}/intervene",
        json={"action": "CANCELLED", "reason": "统计时间戳测试"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200, response.text

    # The request commits through its own session. Refresh the fixture's
    # identity-mapped row before checking persisted state.
    await db_session.refresh(order)
    assert order.status == OrderStatus.CANCELLED
    assert order.cancelled_at is not None


async def test_order_analytics_requires_admin_and_valid_period_details(
    client: AsyncClient,
    registered_user: dict,
    admin_user: dict,
):
    forbidden = await client.get("/admin/order-analytics", headers=auth_header(registered_user))
    assert forbidden.status_code == 403

    invalid_period = await client.get("/admin/order-analytics?period=quarter", headers=auth_header(admin_user))
    assert invalid_period.status_code == 422

    today = datetime.now(timezone.utc).astimezone(PRODUCT_TIMEZONE).date()
    out_of_range = await client.get(
        "/admin/order-analytics/details",
        params={
            "period": "today",
            "date": (today - timedelta(days=1)).isoformat(),
            "metric": "issued",
        },
        headers=auth_header(admin_user),
    )
    assert out_of_range.status_code == 400
