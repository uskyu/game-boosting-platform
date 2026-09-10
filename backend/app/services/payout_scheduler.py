"""到账时效自动结算调度器。

计时起点由「保证金管理」的结账时效模式决定：

- ``AFTER_DELIVERY``（默认）：打手交付后开始计时，到期自动按全额、无扣除
  走与人工审核相同的结算函数（老板仍可提前手动审核，立即打款）；
- ``AFTER_APPROVAL``：老板审核通过后才开始计时，通过后再压档位对应时长结算
  （审核通过不立即入账，由本调度器到期放款）。

保证金模式关闭时退回原有规则：按订单自身的 ``payout_delay_days`` /
``payout_delay_hours`` 从交付时间起算。

``scan_due_payouts`` 是可注入 ``now`` 的扫描函数（便于测试，不依赖真实时间
流逝）；app.main 的 lifespan 启动后台任务每 10 分钟调用。
"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.deposit import (
    SETTLEMENT_MODE_AFTER_APPROVAL,
    DepositTier,
)
from app.models.notification import NotificationType
from app.models.order import ClaimLifecycleStatus, Order, OrderClaim
from app.models.wallet import Wallet
from app.services.order_service import get_order_service

logger = logging.getLogger(__name__)


def _as_utc(value: datetime) -> datetime:
    """Interpret naive database/application datetimes as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _deposit_balances(db: AsyncSession, user_ids: set[int]) -> dict[int, Decimal]:
    """批量取打手的保证金余额，用于解析档位。"""
    if not user_ids:
        return {}
    result = await db.execute(
        select(Wallet.user_id, Wallet.deposit_balance).where(
            Wallet.user_id.in_(user_ids)
        )
    )
    return {user_id: Decimal(str(balance or 0)) for user_id, balance in result.all()}


def _order_delay_due(claim: OrderClaim, order: Order) -> datetime | None:
    """保证金模式关闭时的原有规则：交付时间 + 订单自身的到账时效。"""
    days = order.payout_delay_days
    hours = getattr(order, "payout_delay_hours", None)
    if (days is None and hours is None) or claim.delivered_at is None:
        return None
    return _as_utc(claim.delivered_at) + timedelta(
        days=int(days or 0), hours=int(hours or 0)
    )


def _tier_settle_due(
    claim: OrderClaim,
    tier: DepositTier | None,
    settlement_mode: str,
) -> datetime | None:
    """保证金模式下的到期时间：按命中档位的结账时效计算。"""
    if tier is None:
        return None
    hours = int(tier.settle_hours or 0)
    if settlement_mode == SETTLEMENT_MODE_AFTER_APPROVAL:
        # 老板审核通过后才开始计时
        if claim.approved_at is None:
            return None
        return _as_utc(claim.approved_at) + timedelta(hours=hours)
    if claim.delivered_at is None:
        return None
    return _as_utc(claim.delivered_at) + timedelta(hours=hours)


async def scan_due_payouts(
    db: AsyncSession, *, now: datetime | None = None
) -> list[int]:
    """扫描并自动结算到账时效到期的交付名额。

    每个名额独立 savepoint 结算（全额、deduction=0，走与 review_claim 相同的
    结算函数），失败只 log 不影响其他名额；结算成功后给打手发到账通知。
    """
    now = _as_utc(now or datetime.now(timezone.utc))

    # 延迟导入避免 service -> service 的循环依赖
    from app.services import deposit_service

    deposit_on = await deposit_service.is_deposit_enabled(db)
    settlement_mode = "AFTER_DELIVERY"
    tiers: list[DepositTier] = []
    if deposit_on:
        setting = await deposit_service.get_or_create_deposit_setting(db)
        settlement_mode = setting.settlement_mode
        tiers = await deposit_service.list_tiers(db)

    query = (
        select(OrderClaim, Order)
        .join(Order, OrderClaim.order_id == Order.id)
        .where(OrderClaim.status == ClaimLifecycleStatus.DELIVERED)
    )
    if not deposit_on:
        # 原有行为：只有设置了到账时效的订单才参与自动结算
        query = query.where(
            or_(
                Order.payout_delay_days.isnot(None),
                Order.payout_delay_hours.isnot(None),
            )
        )
    candidates = await db.execute(query.order_by(OrderClaim.id.asc()))

    rows = candidates.all()
    balances = (
        await _deposit_balances(db, {claim.booster_id for claim, _ in rows})
        if deposit_on
        else {}
    )

    settled_claim_ids: list[int] = []
    for claim, order in rows:
        if deposit_on:
            tier = deposit_service.resolve_tier(
                tiers, balances.get(claim.booster_id, Decimal("0"))
            )
            due_at = _tier_settle_due(claim, tier, settlement_mode)
        else:
            due_at = _order_delay_due(claim, order)

        if due_at is None or due_at > now:
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
                    locked_order, locked_claim, delay_from_tier=deposit_on
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
