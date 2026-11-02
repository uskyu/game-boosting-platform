"""
Dashboard analytics service.
Aggregation queries for admin data visualization.
"""

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.product_time import PRODUCT_TIMEZONE, product_day_bounds, product_local_date

from app.models.order import ClaimLifecycleStatus, Order, OrderClaim, OrderStatus, PaymentStatus
from app.models.user import User, UserRole
from app.schemas.dashboard import (
    BoosterRankingResponse,
    BoosterRankItem,
    GameDistributionItem,
    GameDistributionResponse,
    OrderTrendResponse,
    OverviewStats,
    TrendPoint,
    UserGrowthPoint,
    UserGrowthResponse,
)
from app.services import platform_income_service


def _product_date_start_utc(day: date) -> datetime:
    """北京时间自然日 00:00 对应的 naive UTC（与 DB DATETIME 列比较）。"""
    local_midnight = datetime.combine(day, time.min, tzinfo=PRODUCT_TIMEZONE)
    return product_day_bounds(local_midnight)[0]


class DashboardService:
    """Analytics queries for the admin dashboard."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_overview(self) -> OverviewStats:
        """Fetch platform-wide overview statistics."""
        # User counts
        user_count_q = select(func.count()).select_from(User)
        total_users = (await self.db.execute(user_count_q)).scalar_one()

        booster_count_q = select(func.count()).where(User.role != UserRole.ADMIN)
        total_boosters = (await self.db.execute(booster_count_q)).scalar_one()

        # Order counts
        total_orders_q = select(func.count()).select_from(Order)
        total_orders = (await self.db.execute(total_orders_q)).scalar_one()

        status_counts_q = select(
            Order.status,
            func.count().label("cnt"),
        ).group_by(Order.status)
        status_rows = (await self.db.execute(status_counts_q)).all()
        status_map = {row.status: row.cnt for row in status_rows}

        pending = status_map.get(OrderStatus.PENDING, 0)
        active = status_map.get(OrderStatus.LOCKED, 0)
        delivered = status_map.get(OrderStatus.DELIVERED, 0)
        completed = status_map.get(OrderStatus.COMPLETED, 0)
        disputed = status_map.get(OrderStatus.DISPUTED, 0)

        # 成交额：已完结派单的报酬总额（派单平台不走前置支付，按完结口径统计）
        revenue_q = select(func.coalesce(func.sum(Order.price), 0)).where(
            Order.status == OrderStatus.COMPLETED,
        )
        total_revenue = float((await self.db.execute(revenue_q)).scalar_one())

        # 平台服务费：只统计 platform_income（仅后台可见），与成交额分开
        service_fee_total, service_fee_month = await platform_income_service.totals(
            self.db
        )

        return OverviewStats(
            total_users=total_users,
            total_boosters=total_boosters,
            total_orders=total_orders,
            total_revenue=total_revenue,
            service_fee_total=float(service_fee_total),
            service_fee_month=float(service_fee_month),
            pending_orders=pending,
            active_orders=active,
            delivered_orders=delivered,
            completed_orders=completed,
            disputed_orders=disputed,
        )

    async def get_order_trend(self, period: str = "day", days: int = 30) -> OrderTrendResponse:
        """Order creation trend grouped by Asia/Shanghai calendar day/week/month."""
        start_date = product_local_date(datetime.now(timezone.utc)) - timedelta(days=days)
        start_utc = _product_date_start_utc(start_date)
        # DB timestamps are naive UTC. Shift only for calendar labels; filter on the
        # raw column so the created_at index remains usable.
        local_created_at = func.date_add(Order.created_at, text("INTERVAL 8 HOUR"))

        if period == "month":
            date_label = func.date_format(local_created_at, "%Y-%m")
        elif period == "week":
            # MySQL Monday-based week start, calculated after converting to Beijing wall time.
            week_start = text(
                "DATE_SUB(DATE_ADD(orders.created_at, INTERVAL 8 HOUR), "
                "INTERVAL WEEKDAY(DATE_ADD(orders.created_at, INTERVAL 8 HOUR)) DAY)"
            )
            date_label = func.date_format(week_start, "%Y-%m-%d")
        else:
            date_label = func.date_format(local_created_at, "%Y-%m-%d")

        query = (
            select(
                date_label.label("date_label"),
                func.count().label("cnt"),
                func.coalesce(func.sum(Order.price), 0).label("revenue"),
            )
            .where(Order.created_at >= start_utc)
            .group_by("date_label")
            .order_by("date_label")
        )
        rows = (await self.db.execute(query)).all()

        points = [
            TrendPoint(date=row.date_label, count=row.cnt, revenue=float(row.revenue))
            for row in rows
        ]
        return OrderTrendResponse(points=points, period=period)

    async def get_game_distribution(self) -> GameDistributionResponse:
        """Order count and revenue per game."""
        query = (
            select(
                Order.game_name,
                func.count().label("cnt"),
                func.coalesce(func.sum(Order.price), 0).label("revenue"),
            )
            .group_by(Order.game_name)
            .order_by(func.count().desc())
            .limit(20)
        )
        rows = (await self.db.execute(query)).all()

        items = [
            GameDistributionItem(
                game_name=row.game_name,
                count=row.cnt,
                revenue=float(row.revenue),
            )
            for row in rows
        ]
        return GameDistributionResponse(items=items)

    async def get_booster_ranking(self, limit: int = 20) -> BoosterRankingResponse:
        """Top boosters by completed revenue.

        「完成」不能再读 User.total_completed：该字段只有信用分重算时才会刷新，
        线上大量打手长期为 0（2026-11-02 老板反馈）。这里直接按名额结算口径
        实时聚合，遗留订单再用 Order.status=COMPLETED 的计数兜底。
        """
        # 现行名额制：已结算名额数 / 已结算金额
        claim_stats = (
            select(
                OrderClaim.booster_id.label("booster_id"),
                func.count(OrderClaim.id).label("completed_count"),
                func.coalesce(func.sum(Order.price), 0).label("settled_revenue"),
            )
            .join(Order, Order.id == OrderClaim.order_id)
            .where(
                OrderClaim.status == ClaimLifecycleStatus.SETTLED,
                Order.status != OrderStatus.CANCELLED,
            )
            .group_by(OrderClaim.booster_id)
            .subquery()
        )

        # 迁移前/单名额历史单可能没有结算名额记录，按订单完成数兜底
        legacy_completed = (
            select(
                Order.booster_id.label("booster_id"),
                func.count(Order.id).label("legacy_completed_count"),
                func.coalesce(func.sum(Order.price), 0).label("legacy_revenue"),
            )
            .where(
                Order.status == OrderStatus.COMPLETED,
                Order.booster_id.is_not(None),
            )
            .group_by(Order.booster_id)
            .subquery()
        )

        completed_count_expr = func.greatest(
            func.coalesce(claim_stats.c.completed_count, 0),
            func.coalesce(legacy_completed.c.legacy_completed_count, 0),
        )
        revenue_expr = func.greatest(
            func.coalesce(claim_stats.c.settled_revenue, 0),
            func.coalesce(legacy_completed.c.legacy_revenue, 0),
        )

        query = (
            select(
                User.id,
                User.username,
                User.avatar_url,
                User.credit_score,
                User.credit_level,
                completed_count_expr.label("total_completed"),
                User.avg_rating,
                revenue_expr.label("total_revenue"),
            )
            .outerjoin(claim_stats, User.id == claim_stats.c.booster_id)
            .outerjoin(legacy_completed, User.id == legacy_completed.c.booster_id)
            .where(User.role != UserRole.ADMIN)
            .order_by(
                revenue_expr.desc(),
                completed_count_expr.desc(),
                User.credit_score.desc(),
            )
            .limit(limit)
        )
        rows = (await self.db.execute(query)).all()

        items = [
            BoosterRankItem(
                user_id=row.id,
                username=row.username,
                avatar_url=row.avatar_url,
                credit_score=row.credit_score,
                credit_level=row.credit_level,
                total_completed=int(row.total_completed or 0),
                avg_rating=float(row.avg_rating),
                total_revenue=float(row.total_revenue),
            )
            for row in rows
        ]
        return BoosterRankingResponse(items=items)

    async def get_user_growth(self, days: int = 30) -> UserGrowthResponse:
        """Daily new-user trend grouped by Asia/Shanghai calendar date."""
        start_date = product_local_date(datetime.now(timezone.utc)) - timedelta(days=days)
        start_utc = _product_date_start_utc(start_date)
        local_created_at = func.date_add(User.created_at, text("INTERVAL 8 HOUR"))

        query = (
            select(
                func.date_format(local_created_at, "%Y-%m-%d").label("date_label"),
                func.count().label("new_users"),
            )
            .where(User.created_at >= start_utc)
            .group_by("date_label")
            .order_by("date_label")
        )
        rows = (await self.db.execute(query)).all()

        # Get cumulative count before the Beijing-day start boundary.
        base_count_q = select(func.count()).where(User.created_at < start_utc)
        base_count = (await self.db.execute(base_count_q)).scalar_one()

        cumulative = base_count
        points = []
        for row in rows:
            cumulative += row.new_users
            points.append(
                UserGrowthPoint(
                    date=row.date_label,
                    new_users=row.new_users,
                    cumulative=cumulative,
                )
            )

        return UserGrowthResponse(points=points)


def get_dashboard_service(db: AsyncSession) -> DashboardService:
    """Factory for DashboardService."""
    return DashboardService(db)
