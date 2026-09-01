"""
Dashboard analytics service.
Aggregation queries for admin data visualization.
"""

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order, OrderStatus, PaymentStatus
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


class DashboardService:
    """Analytics queries for the admin dashboard."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_overview(self) -> OverviewStats:
        """Fetch platform-wide overview statistics."""
        # User counts
        user_count_q = select(func.count()).select_from(User)
        total_users = (await self.db.execute(user_count_q)).scalar_one()

        booster_count_q = select(func.count()).where(User.role == UserRole.BOOSTER)
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
        active = status_map.get(OrderStatus.LOCKED, 0) + status_map.get(OrderStatus.DELIVERED, 0)
        completed = status_map.get(OrderStatus.COMPLETED, 0)
        disputed = status_map.get(OrderStatus.DISPUTED, 0)

        # Revenue from completed + paid orders
        revenue_q = select(func.coalesce(func.sum(Order.price), 0)).where(
            Order.status == OrderStatus.COMPLETED,
            Order.payment_status == PaymentStatus.PAID,
        )
        total_revenue = float((await self.db.execute(revenue_q)).scalar_one())

        return OverviewStats(
            total_users=total_users,
            total_boosters=total_boosters,
            total_orders=total_orders,
            total_revenue=total_revenue,
            pending_orders=pending,
            active_orders=active,
            completed_orders=completed,
            disputed_orders=disputed,
        )

    async def get_order_trend(self, period: str = "day", days: int = 30) -> OrderTrendResponse:
        """Order creation trend grouped by day/week/month."""
        start_date = date.today() - timedelta(days=days)

        if period == "month":
            date_label = func.date_format(Order.created_at, "%Y-%m")
        elif period == "week":
            date_label = func.date_format(
                Order.created_at - func.weekday(Order.created_at), "%Y-%m-%d"
            )
        else:
            date_label = func.date_format(Order.created_at, "%Y-%m-%d")

        query = (
            select(
                date_label.label("date_label"),
                func.count().label("cnt"),
                func.coalesce(func.sum(Order.price), 0).label("revenue"),
            )
            .where(func.date(Order.created_at) >= start_date)
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
        """Top boosters by completed orders and revenue."""
        # Subquery for per-booster revenue
        revenue_sub = (
            select(
                Order.booster_id,
                func.coalesce(func.sum(Order.price), 0).label("total_revenue"),
            )
            .where(Order.status == OrderStatus.COMPLETED)
            .group_by(Order.booster_id)
            .subquery()
        )

        query = (
            select(
                User.id,
                User.username,
                User.avatar_url,
                User.credit_score,
                User.credit_level,
                User.total_completed,
                User.avg_rating,
                func.coalesce(revenue_sub.c.total_revenue, 0).label("total_revenue"),
            )
            .outerjoin(revenue_sub, User.id == revenue_sub.c.booster_id)
            .where(User.role.in_([UserRole.BOOSTER, UserRole.ADMIN]))
            .order_by(User.total_completed.desc(), User.credit_score.desc())
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
                total_completed=row.total_completed,
                avg_rating=float(row.avg_rating),
                total_revenue=float(row.total_revenue),
            )
            for row in rows
        ]
        return BoosterRankingResponse(items=items)

    async def get_user_growth(self, days: int = 30) -> UserGrowthResponse:
        """Daily new user registration trend."""
        start_date = date.today() - timedelta(days=days)

        query = (
            select(
                func.date_format(User.created_at, "%Y-%m-%d").label("date_label"),
                func.count().label("new_users"),
            )
            .where(func.date(User.created_at) >= start_date)
            .group_by("date_label")
            .order_by("date_label")
        )
        rows = (await self.db.execute(query)).all()

        # Get cumulative count before the start_date
        base_count_q = select(func.count()).where(
            func.date(User.created_at) < start_date
        )
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
