"""到账时效自动结算调度器。

订单可设置 payout_delay_days（1-5 天）：打手交付（claim 变为
DELIVERED）后，经过该天数自动按全额、无扣除走与人工审核相同的
结算函数，并给打手发送到账通知。

scan_due_payouts 是可注入 ``now`` 的扫描函数（便于测试，不依赖
真实时间流逝）；app.main 的 lifespan 启动后台任务每 10 分钟调用。
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import NotificationType
from app.models.order import ClaimLifecycleStatus, Order, OrderClaim
from app.services.order_service import get_order_service

logger = logging.getLogger(__name__)


def _as_utc(value: datetime) -> datetime:
    """MySQL DATETIME 返回 naive 时间，补 UTC 再参与比较。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _claim_due(claim: OrderClaim, order: Order, now: datetime) -> bool:
    """该名额的到账时效是否已到期。"""
    if order.payout_delay_days is None or claim.delivered_at is None:
        return False
    due_at = _as_utc(claim.delivered_at) + timedelta(days=int(order.payout_delay_days))
    return due_at <= now


async def scan_due_payouts(
    db: AsyncSession, *, now: datetime | None = None
) -> list[int]:
    """扫描并自动结算到账时效到期的交付名额。

    - 候选：status=DELIVERED 且所属订单设置了 payout_delay_days；
    - 到期判定：delivered_at + payout_delay_days 天 <= now（now 可注入）；
    - 每个名额独立 savepoint 结算（全额、deduction=0，走与
      review_claim 相同的结算函数），失败只 log 不影响其他名额；
    - 结算成功后给打手发到账通知（尽力而为）。

    Returns:
        本次成功结算的 claim id 列表。
    """
    now = now or datetime.now(timezone.utc)

    candidates = await db.execute(
        select(OrderClaim, Order)
        .join(Order, OrderClaim.order_id == Order.id)
        .where(
            OrderClaim.status == ClaimLifecycleStatus.DELIVERED,
            Order.payout_delay_days.isnot(None),
        )
        .order_by(OrderClaim.id.asc())
    )

    settled_claim_ids: list[int] = []
    for claim, order in candidates.all():
        if not _claim_due(claim, order, now):
            continue
        try:
            async with db.begin_nested():
                # 锁定订单与名额行后再结算，避免与人工审核并发
                locked_order = (
                    await db.execute(
                        select(Order).where(Order.id == order.id).with_for_update()
                    )
                ).scalar_one()
                locked_claim = (
                    await db.execute(
                        select(OrderClaim)
                        .where(OrderClaim.id == claim.id)
                        .with_for_update()
                    )
                ).scalar_one()
                order_service = get_order_service(db)
                done = await order_service.auto_settle_due_claim(
                    locked_order, locked_claim
                )
            if done:
                settled_claim_ids.append(claim.id)
        except Exception as exc:
            logger.warning(
                "Payout delay auto-settle failed for claim %s (order %s): %s",
                claim.id,
                order.id,
                exc,
            )
            continue

        # 到账通知（尽力而为，失败不影响结算结果）
        try:
            await _notify_auto_settled(db, order, claim)
        except Exception as exc:
            logger.warning(
                "Payout settle notification failed for claim %s: %s", claim.id, exc
            )

    if settled_claim_ids:
        logger.info(
            "Payout delay scan settled %s claims: %s",
            len(settled_claim_ids),
            settled_claim_ids,
        )
    return settled_claim_ids


async def _notify_auto_settled(
    db: AsyncSession, order: Order, claim: OrderClaim
) -> Any:
    """结算成功后通知打手报酬已到账。"""
    # 延迟导入避免 service -> api 的循环依赖
    from app.api.notification_utils import notify_user

    return await notify_user(
        db,
        user_id=claim.booster_id,
        type=NotificationType.ORDER_CONFIRMED,
        title="订单已自动结算",
        content=f"订单「{order.game_name}」已到账时效自动结算，报酬已入账",
        link=f"/orders/{order.id}",
        ref_id=order.id,
    )
