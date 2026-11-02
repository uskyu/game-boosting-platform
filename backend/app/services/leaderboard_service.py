"""打手排行榜：按周期内“已接单金额”排序。

口径（2026-11-02 老板确认）：
- 接手即计入，不要求已经提交结单/完成；
- 订单取消后从榜单金额中扣除（取消名额/取消订单都不计）；
- 日榜按北京时间自然日、周榜按北京时间周一 00:00 起算、总榜不限时间；
- 只展示前 30 名，并返回当前用户自己的名次与距上一名的金额差。
"""

from datetime import datetime, time, timedelta, timezone
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.product_time import PRODUCT_TIMEZONE, product_local_date
from app.models.order import (
    ClaimLifecycleStatus,
    Order,
    OrderClaim,
    OrderStatus,
)
from app.models.user import User, UserRole
from app.schemas.leaderboard import (
    LeaderboardEntry,
    LeaderboardMe,
    LeaderboardResponse,
)

LeaderboardPeriod = Literal["day", "week", "all"]

TOP_LIMIT = 30


def _period_start_utc(period: LeaderboardPeriod, now: datetime) -> datetime | None:
    """周期起点（naive UTC）；总榜返回 None。"""
    local_today = product_local_date(now)
    if period == "day":
        local_start = local_today
    elif period == "week":
        local_start = local_today - timedelta(days=local_today.weekday())
    else:
        return None

    local_midnight = datetime.combine(local_start, time.min, tzinfo=PRODUCT_TIMEZONE)
    return local_midnight.astimezone(timezone.utc).replace(tzinfo=None)


class LeaderboardService:
    """按接单金额聚合排行榜。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_leaderboard(
        self,
        current_user: User,
        period: LeaderboardPeriod = "day",
    ) -> LeaderboardResponse:
        start_utc = _period_start_utc(period, datetime.now(timezone.utc))

        amount_expr = func.coalesce(func.sum(Order.price), 0)
        query = (
            select(
                OrderClaim.booster_id.label("user_id"),
                User.username.label("username"),
                User.avatar_url.label("avatar_url"),
                amount_expr.label("amount"),
                func.min(OrderClaim.created_at).label("first_claim_at"),
            )
            .select_from(OrderClaim)
            .join(Order, OrderClaim.order_id == Order.id)
            .join(User, OrderClaim.booster_id == User.id)
            # 取消的名额/取消的订单都不计入；接单后取消会自动扣回。
            .where(OrderClaim.status != ClaimLifecycleStatus.CANCELLED)
            .where(Order.status != OrderStatus.CANCELLED)
            .where(User.role != UserRole.ADMIN)
            .group_by(OrderClaim.booster_id, User.username, User.avatar_url)
            .having(amount_expr > 0)
        )
        if start_utc is not None:
            query = query.where(OrderClaim.created_at >= start_utc)

        rows = (await self.db.execute(query)).all()

        ranked = sorted(
            rows,
            key=lambda row: (
                -float(row.amount or 0),
                row.first_claim_at or datetime.min,
                row.user_id,
            ),
        )

        items = [
            LeaderboardEntry(
                rank=index + 1,
                user_id=row.user_id,
                username=row.username,
                avatar_url=row.avatar_url,
                amount=round(float(row.amount or 0), 2),
            )
            for index, row in enumerate(ranked[:TOP_LIMIT])
        ]

        me = LeaderboardMe()
        for index, row in enumerate(ranked):
            if row.user_id != current_user.id:
                continue
            my_amount = round(float(row.amount or 0), 2)
            gap = None
            if index > 0:
                previous_amount = round(float(ranked[index - 1].amount or 0), 2)
                gap = round(max(previous_amount - my_amount, 0.0), 2)
            me = LeaderboardMe(
                rank=index + 1,
                amount=my_amount,
                gap_to_previous=gap,
            )
            break

        return LeaderboardResponse(period=period, items=items, me=me)


def get_leaderboard_service(db: AsyncSession) -> LeaderboardService:
    return LeaderboardService(db)
