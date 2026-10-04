"""
今日已接单（大厅区块）服务。

大厅「今日已接单」区块的数据源：今天已被抢的订单（OrderClaim），
一条 join 查询带出订单摘要、打手摘要与打手保证金余额，禁止 N+1：

    OrderClaim
    JOIN orders       ON order_claims.order_id  = orders.id
    JOIN users        ON order_claims.booster_id = users.id   （打手）
    LEFT JOIN wallets ON wallets.user_id        = users.id

「今天」的口径与 dashboard_service 现有按日统计保持一致：用数据库函数
取 created_at 的日期（func.date），与 Python 进程本地今天的日期比较。
（MySQL DATETIME 约定存上海墙钟时间，服务端进程按该时区运行，两侧同源。）
"""

from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import ClaimLifecycleStatus, Order, OrderClaim
from app.models.user import User
from app.models.wallet import Wallet

# 时间范围：当前仅支持「今天」，为将来（本周/本月）预留取值位
SCOPE_TODAY = "today"


class RecentClaimsService:
    """Queries behind the hall's 今日已接单 block."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _conditions(self, scope: str) -> list[Any]:
        """按 scope 生成过滤条件；口径与 dashboard_service 按日统计一致。"""
        if scope == SCOPE_TODAY:
            return [func.date(OrderClaim.created_at) >= date.today()]
        raise ValueError(f"不支持的时间范围: {scope}")

    async def list_recent_claims(
        self,
        scope: str = SCOPE_TODAY,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """今天的接单记录（按 claim id 倒序）+ 总数。"""
        conditions = self._conditions(scope)

        total_result = await self.db.execute(
            select(func.count(OrderClaim.id))
            .select_from(OrderClaim)
            .join(Order, OrderClaim.order_id == Order.id)
            .where(*conditions)
        )
        total = int(total_result.scalar() or 0)

        paid_claim_counts = (
            select(
                OrderClaim.booster_id.label("booster_id"),
                func.count(OrderClaim.id).label("paid_order_count"),
            )
            .where(OrderClaim.status == ClaimLifecycleStatus.SETTLED)
            .group_by(OrderClaim.booster_id)
            .subquery()
        )

        result = await self.db.execute(
            select(
                OrderClaim.id.label("claim_id"),
                OrderClaim.created_at.label("created_at"),
                Order.id.label("order_id"),
                Order.title.label("order_title"),
                Order.intro.label("order_intro"),
                Order.price.label("order_price"),
                User.id.label("booster_id"),
                User.username.label("booster_username"),
                func.coalesce(
                    paid_claim_counts.c.paid_order_count, 0
                ).label("booster_total_completed"),
                func.coalesce(Wallet.deposit_balance, 0).label("deposit_balance"),
            )
            .select_from(OrderClaim)
            .join(Order, OrderClaim.order_id == Order.id)
            .join(User, OrderClaim.booster_id == User.id)
            .outerjoin(paid_claim_counts, paid_claim_counts.c.booster_id == User.id)
            .outerjoin(Wallet, Wallet.user_id == User.id)
            .where(*conditions)
            .order_by(OrderClaim.id.desc())
            .limit(limit)
            .offset(offset)
        )

        items: list[dict[str, Any]] = [
            {
                "id": row.claim_id,
                "created_at": row.created_at,
                "order": {
                    "id": row.order_id,
                    "title": row.order_title,
                    "intro": row.order_intro,
                    "price": row.order_price,
                },
                "booster": {
                    "id": row.booster_id,
                    "username": row.booster_username,
                    "total_completed": row.booster_total_completed,
                },
                "deposit_balance": row.deposit_balance,
            }
            for row in result.all()
        ]
        return items, total


def get_recent_claims_service(db: AsyncSession) -> RecentClaimsService:
    """Factory for RecentClaimsService."""
    return RecentClaimsService(db)
