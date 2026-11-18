"""Admin order activity analytics, grouped by Asia/Shanghai calendar days."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Literal

from sqlalchemy import case, exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.product_time import PRODUCT_TIMEZONE
from app.models.order import ClaimLifecycleStatus, Order, OrderClaim, OrderStatus
from app.models.user import User
from app.schemas.serializers import serialize_datetime_utc

AnalyticsPeriod = Literal["today", "7d", "30d"]
AnalyticsMetric = Literal["issued", "issued_amount", "cancelled", "completed", "settled"]

PERIOD_DAYS: dict[str, int] = {"today": 1, "7d": 7, "30d": 30}


@dataclass(frozen=True)
class PeriodBounds:
    period: AnalyticsPeriod
    start_date: date
    end_date: date
    start_utc: datetime
    end_utc: datetime


def _naive_utc_day_bounds(day: date) -> tuple[datetime, datetime]:
    start_local = datetime.combine(day, time.min, tzinfo=PRODUCT_TIMEZONE)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, start_utc + timedelta(days=1)


def period_bounds(period: AnalyticsPeriod, *, today: date | None = None) -> PeriodBounds:
    """Return an inclusive product-local date range and its UTC DB bounds."""
    current_day = today or datetime.now(timezone.utc).astimezone(PRODUCT_TIMEZONE).date()
    start_day = current_day - timedelta(days=PERIOD_DAYS[period] - 1)
    start_utc, _ = _naive_utc_day_bounds(start_day)
    _, end_utc = _naive_utc_day_bounds(current_day)
    return PeriodBounds(
        period=period,
        start_date=start_day,
        end_date=current_day,
        start_utc=start_utc,
        end_utc=end_utc,
    )


def _day_label(column):
    """MySQL expression mapping naive UTC DATETIME to a Beijing date string."""
    return func.date_format(func.date_add(column, text("INTERVAL 8 HOUR")), "%Y-%m-%d")


def _event_range(column, start: datetime, end: datetime):
    return (column >= start, column < end)


def _as_money(value) -> float:
    return round(float(value or Decimal("0.00")), 2)


def _empty_daily(bounds: PeriodBounds) -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    day = bounds.start_date
    while day <= bounds.end_date:
        rows[day.isoformat()] = {
            "date": day,
            "issued_orders": 0,
            "cancelled_orders": 0,
            "completed_orders": 0,
            "settled_slots": 0,
            "issued_amount": 0.0,
            "successful_amount": 0.0,
        }
        day += timedelta(days=1)
    return rows


async def _group_order_event_counts(
    db: AsyncSession,
    *,
    timestamp_column,
    bounds: PeriodBounds,
    status_filter: OrderStatus | None = None,
    publisher_id: int | None = None,
) -> dict[str, int]:
    label = _day_label(timestamp_column)
    conditions = list(_event_range(timestamp_column, bounds.start_utc, bounds.end_utc))
    if status_filter is not None:
        conditions.append(Order.status == status_filter)
    if publisher_id is not None:
        conditions.append(Order.user_id == publisher_id)
    rows = (
        await db.execute(
            select(label.label("day"), func.count(Order.id).label("count")).where(*conditions).group_by(label)
        )
    ).all()
    return {str(row.day): int(row.count or 0) for row in rows}


async def get_order_analytics(
    db: AsyncSession,
    period: AnalyticsPeriod,
    *,
    publisher_id: int | None = None,
) -> dict[str, object]:
    """Return order activity totals and one row per product-local day.

    Event definitions are deliberately separate:
    - issued: ``Order.created_at``
    - cancelled: ``Order.cancelled_at`` for orders still in CANCELLED state
    - completed: ``Order.completed_at`` for orders in COMPLETED state
    - successful amount: gross value of claims settled in-period (price per slot),
      plus one-price legacy COMPLETED orders that predate claim rows.
    """
    bounds = period_bounds(period)
    daily = _empty_daily(bounds)

    issued = await _group_order_event_counts(
        db,
        timestamp_column=Order.created_at,
        bounds=bounds,
        publisher_id=publisher_id,
    )
    cancelled = await _group_order_event_counts(
        db,
        timestamp_column=Order.cancelled_at,
        bounds=bounds,
        status_filter=OrderStatus.CANCELLED,
        publisher_id=publisher_id,
    )
    completed = await _group_order_event_counts(
        db,
        timestamp_column=Order.completed_at,
        bounds=bounds,
        status_filter=OrderStatus.COMPLETED,
        publisher_id=publisher_id,
    )
    for day, count in issued.items():
        if day in daily:
            daily[day]["issued_orders"] = count
    for day, count in cancelled.items():
        if day in daily:
            daily[day]["cancelled_orders"] = count
    for day, count in completed.items():
        if day in daily:
            daily[day]["completed_orders"] = count

    issued_label = _day_label(Order.created_at)
    issued_conditions = list(_event_range(Order.created_at, bounds.start_utc, bounds.end_utc))
    issued_conditions.append(Order.status != OrderStatus.CANCELLED)
    if publisher_id is not None:
        issued_conditions.append(Order.user_id == publisher_id)
    issued_amount_rows = (
        await db.execute(
            select(
                issued_label.label("day"),
                func.coalesce(func.sum(Order.price * func.coalesce(Order.max_claims, 1)), 0).label("amount"),
            )
            .where(*issued_conditions)
            .group_by(issued_label)
        )
    ).all()
    for row in issued_amount_rows:
        day = str(row.day)
        if day in daily:
            daily[day]["issued_amount"] = _as_money(row.amount)

    settled_label = _day_label(OrderClaim.settled_at)
    claim_conditions = [
        OrderClaim.status == ClaimLifecycleStatus.SETTLED,
        OrderClaim.settled_at >= bounds.start_utc,
        OrderClaim.settled_at < bounds.end_utc,
    ]
    if publisher_id is not None:
        claim_conditions.append(Order.user_id == publisher_id)
    claim_rows = (
        await db.execute(
            select(
                settled_label.label("day"),
                func.count(OrderClaim.id).label("slots"),
                func.coalesce(func.sum(Order.price), 0).label("amount"),
            )
            .join(Order, Order.id == OrderClaim.order_id)
            .where(*claim_conditions)
            .group_by(settled_label)
        )
    ).all()
    for row in claim_rows:
        day = str(row.day)
        if day in daily:
            daily[day]["settled_slots"] = int(row.slots or 0)
            daily[day]["successful_amount"] = _as_money(row.amount)

    # Old one-slot orders can be COMPLETED without an order_claims row. Count
    # those once at the order price; never double count an order with claims.
    has_claim = exists(select(OrderClaim.id).where(OrderClaim.order_id == Order.id))
    legacy_conditions = [
        Order.status == OrderStatus.COMPLETED,
        Order.completed_at >= bounds.start_utc,
        Order.completed_at < bounds.end_utc,
        ~has_claim,
    ]
    if publisher_id is not None:
        legacy_conditions.append(Order.user_id == publisher_id)
    legacy_label = _day_label(Order.completed_at)
    legacy_rows = (
        await db.execute(
            select(
                legacy_label.label("day"),
                func.count(Order.id).label("orders"),
                func.coalesce(func.sum(Order.price), 0).label("amount"),
            )
            .where(*legacy_conditions)
            .group_by(legacy_label)
        )
    ).all()
    for row in legacy_rows:
        day = str(row.day)
        if day in daily:
            daily[day]["settled_slots"] = int(daily[day]["settled_slots"]) + int(row.orders or 0)
            daily[day]["successful_amount"] = _as_money(float(daily[day]["successful_amount"]) + _as_money(row.amount))

    daily_rows = list(daily.values())
    return {
        "period": period,
        "timezone": "Asia/Shanghai",
        "period_start": bounds.start_date,
        "period_end": bounds.end_date,
        "summary": {
            "period_start": bounds.start_date,
            "period_end": bounds.end_date,
            "issued_orders": sum(int(row["issued_orders"]) for row in daily_rows),
            "cancelled_orders": sum(int(row["cancelled_orders"]) for row in daily_rows),
            "completed_orders": sum(int(row["completed_orders"]) for row in daily_rows),
            "settled_slots": sum(int(row["settled_slots"]) for row in daily_rows),
            "issued_amount": _as_money(sum(float(row["issued_amount"]) for row in daily_rows)),
            "successful_amount": _as_money(sum(float(row["successful_amount"]) for row in daily_rows)),
        },
        "daily": daily_rows,
    }


async def get_publisher_lifetime_analytics(db: AsyncSession, *, publisher_id: int) -> dict[str, object]:
    """Return lifetime order counts and money for one authenticated publisher."""
    has_claim = exists(select(OrderClaim.id).where(OrderClaim.order_id == Order.id))
    order_totals = (
        await db.execute(
            select(
                func.count(Order.id).label("issued_orders"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                Order.status != OrderStatus.CANCELLED,
                                Order.price * func.coalesce(Order.max_claims, 1),
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("issued_amount"),
                func.coalesce(
                    func.sum(
                        case(
                            (Order.status == OrderStatus.CANCELLED, 1),
                            else_=0,
                        )
                    ),
                    0,
                ).label("cancelled_orders"),
                func.coalesce(
                    func.sum(
                        case(
                            (Order.status == OrderStatus.COMPLETED, 1),
                            else_=0,
                        )
                    ),
                    0,
                ).label("completed_orders"),
            ).where(Order.user_id == publisher_id)
        )
    ).one()

    settled_totals = (
        await db.execute(
            select(
                func.count(OrderClaim.id).label("settled_slots"),
                func.coalesce(func.sum(Order.price), 0).label("successful_amount"),
            )
            .join(Order, Order.id == OrderClaim.order_id)
            .where(
                Order.user_id == publisher_id,
                OrderClaim.status == ClaimLifecycleStatus.SETTLED,
            )
        )
    ).one()
    legacy_totals = (
        await db.execute(
            select(
                func.count(Order.id).label("settled_slots"),
                func.coalesce(func.sum(Order.price), 0).label("successful_amount"),
            ).where(
                Order.user_id == publisher_id,
                Order.status == OrderStatus.COMPLETED,
                ~has_claim,
            )
        )
    ).one()

    return {
        "issued_orders": int(order_totals.issued_orders or 0),
        "cancelled_orders": int(order_totals.cancelled_orders or 0),
        "completed_orders": int(order_totals.completed_orders or 0),
        "settled_slots": int(settled_totals.settled_slots or 0) + int(legacy_totals.settled_slots or 0),
        "issued_amount": _as_money(order_totals.issued_amount),
        "successful_amount": _as_money(
            _as_money(settled_totals.successful_amount) + _as_money(legacy_totals.successful_amount)
        ),
    }


async def get_order_analytics_details(
    db: AsyncSession,
    *,
    period: AnalyticsPeriod,
    day: date,
    metric: AnalyticsMetric,
    page: int,
    page_size: int,
    publisher_id: int | None = None,
) -> dict[str, object]:
    """Return paged order-level detail for one metric on one product day."""
    bounds = period_bounds(period)
    if day < bounds.start_date or day > bounds.end_date:
        raise ValueError("详情日期必须位于所选统计周期内")
    day_start, day_end = _naive_utc_day_bounds(day)

    metric_column = {
        "issued": Order.created_at,
        "issued_amount": Order.created_at,
        "cancelled": Order.cancelled_at,
        "completed": Order.completed_at,
    }
    event_timestamp = metric_column.get(metric)
    if metric == "settled":
        settled_order_ids = (
            select(OrderClaim.order_id.label("order_id"))
            .where(
                OrderClaim.status == ClaimLifecycleStatus.SETTLED,
                OrderClaim.settled_at >= day_start,
                OrderClaim.settled_at < day_end,
            )
            .group_by(OrderClaim.order_id)
        )
        has_claim = exists(select(OrderClaim.id).where(OrderClaim.order_id == Order.id))
        legacy_order_ids = select(Order.id.label("order_id")).where(
            Order.status == OrderStatus.COMPLETED,
            Order.completed_at >= day_start,
            Order.completed_at < day_end,
            ~has_claim,
        )
        if publisher_id is not None:
            settled_order_ids = settled_order_ids.join(Order, Order.id == OrderClaim.order_id).where(
                Order.user_id == publisher_id
            )
            legacy_order_ids = legacy_order_ids.where(Order.user_id == publisher_id)
        matching_ids = settled_order_ids.union(legacy_order_ids).subquery()
        total = int(await db.scalar(select(func.count()).select_from(matching_ids)) or 0)
        query = (
            select(Order, User.username)
            .join(User, User.id == Order.user_id)
            .where(Order.id.in_(select(matching_ids.c.order_id)))
            .order_by(Order.id.desc())
        )
    else:
        assert event_timestamp is not None
        conditions = list(_event_range(event_timestamp, day_start, day_end))
        if publisher_id is not None:
            conditions.append(Order.user_id == publisher_id)
        if metric == "cancelled":
            conditions.append(Order.status == OrderStatus.CANCELLED)
        elif metric == "completed":
            conditions.append(Order.status == OrderStatus.COMPLETED)
        elif metric == "issued_amount":
            conditions.append(Order.status != OrderStatus.CANCELLED)
        total = int(await db.scalar(select(func.count(Order.id)).where(*conditions)) or 0)
        query = (
            select(Order, User.username)
            .join(User, User.id == Order.user_id)
            .where(*conditions)
            .order_by(event_timestamp.desc(), Order.id.desc())
        )

    rows = (await db.execute(query.offset((page - 1) * page_size).limit(page_size))).all()
    orders = [row[0] for row in rows]
    order_ids = [order.id for order in orders]

    settled_counts: dict[int, int] = {}
    if order_ids:
        settled_rows = (
            await db.execute(
                select(OrderClaim.order_id, func.count(OrderClaim.id))
                .where(
                    OrderClaim.order_id.in_(order_ids),
                    OrderClaim.status == ClaimLifecycleStatus.SETTLED,
                )
                .group_by(OrderClaim.order_id)
            )
        ).all()
        settled_counts = {int(order_id): int(count) for order_id, count in settled_rows}

    metric_settled_counts: dict[int, int] = {}
    metric_settled_at: dict[int, datetime] = {}
    if metric == "settled" and order_ids:
        metric_rows = (
            await db.execute(
                select(
                    OrderClaim.order_id,
                    func.count(OrderClaim.id),
                    func.max(OrderClaim.settled_at),
                )
                .where(
                    OrderClaim.order_id.in_(order_ids),
                    OrderClaim.status == ClaimLifecycleStatus.SETTLED,
                    OrderClaim.settled_at >= day_start,
                    OrderClaim.settled_at < day_end,
                )
                .group_by(OrderClaim.order_id)
            )
        ).all()
        metric_settled_counts = {int(order_id): int(count) for order_id, count, _ in metric_rows}
        metric_settled_at = {
            int(order_id): settled_at for order_id, _, settled_at in metric_rows if settled_at is not None
        }

    items: list[dict[str, object]] = []
    for order, publisher_name in rows:
        settled_slots = settled_counts.get(order.id, 0)
        price = Decimal(str(order.price or 0))
        total_value = price * int(order.max_claims or 1)
        if settled_slots == 0 and order.status == OrderStatus.COMPLETED:
            # Legacy one-slot COMPLETED order without claim rows.
            settled_slots = 1
        if metric == "settled":
            metric_slots = metric_settled_counts.get(order.id, 0)
            metric_amount = price * metric_slots
            if metric_slots == 0 and order.status == OrderStatus.COMPLETED:
                metric_slots = 1
                metric_amount = price
            event_at = metric_settled_at.get(order.id) or order.completed_at
        elif metric == "completed":
            metric_slots = settled_slots
            metric_amount = price * settled_slots if settled_slots else price
            event_at = order.completed_at
        elif metric == "cancelled":
            metric_slots = 0
            metric_amount = total_value
            event_at = order.cancelled_at
        elif metric == "issued_amount":
            metric_slots = 0
            metric_amount = total_value
            event_at = order.created_at
        else:
            metric_slots = 0
            metric_amount = total_value
            event_at = order.created_at

        items.append(
            {
                "order_id": order.id,
                "title": order.title or f"{order.game_name}订单",
                "game_name": order.game_name,
                "publisher": publisher_name,
                "created_at": serialize_datetime_utc(order.created_at),
                "event_at": serialize_datetime_utc(event_at),
                "status": order.status.value,
                "order_status": order.status.value,
                "unit_price": _as_money(price),
                "max_claims": int(order.max_claims or 1),
                "settled_slots": int(settled_slots),
                "metric_slots": int(metric_slots),
                "metric_amount": _as_money(metric_amount),
                "amount": _as_money(metric_amount),
            }
        )

    return {
        "items": items,
        "total": total,
        "page": page,
        "pages": (total + page_size - 1) // page_size,
        "page_size": page_size,
    }
