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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.deposit import (
    SETTLEMENT_MODE_AFTER_APPROVAL,
    SETTLEMENT_MODE_AFTER_DELIVERY,
    SETTLEMENT_MODE_ORDER_DELAY,
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


def _order_delay_due(claim: OrderClaim, order: Order) -> datetime | None:
    """保证金模式关闭时的原有规则：交付时间 + 订单自身的到账时效。"""
    days = order.payout_delay_days
    hours = getattr(order, "payout_delay_hours", None)
    if (days is None and hours is None) or claim.delivered_at is None:
        return None
    return _as_utc(claim.delivered_at) + timedelta(
        days=int(days or 0), hours=int(hours or 0)
    )


def _snapshot_settle_due(claim: OrderClaim) -> datetime | None:
    """Return the immutable due time captured on a post-migration claim."""
    if claim.settlement_mode_snapshot is None or claim.settlement_due_at is None:
        return None
    return _as_utc(claim.settlement_due_at)


def _legacy_deposit_due(
    claim: OrderClaim,
    tier: DepositTier | None,
    settlement_mode: str,
) -> datetime | None:
    """Recover the pre-033 deposit due time without guessing.

    Before the snapshot migration, deposit-enabled claims used the active tier
    at scan time. Reproduce that old rule only when the required source time is
    present: delivered_at for AFTER_DELIVERY, or approved_at for
    AFTER_APPROVAL. A missing approval timestamp is intentionally not treated
    as immediately due.
    """
    if tier is None:
        return None
    if settlement_mode == SETTLEMENT_MODE_AFTER_APPROVAL:
        if claim.approved_at is None:
            return None
        started_at = claim.approved_at
    else:
        if claim.delivered_at is None:
            return None
        started_at = claim.delivered_at
    return _as_utc(started_at) + timedelta(hours=max(int(tier.settle_hours or 0), 0))


def _legacy_due(
    claim: OrderClaim,
    order: Order,
    *,
    deposit_enabled: bool,
    tier: DepositTier | None,
    settlement_mode: str,
) -> datetime | None:
    """Resolve a pre-033 claim using the old scheduler contract.

    Legacy deposit-enabled claims used tier timing first.  The old order-delay
    rule applies only when deposit mode was disabled, preserving both the old
    AFTER_APPROVAL ``approved_at`` origin and the proper tier gate.
    """
    if deposit_enabled:
        return _legacy_deposit_due(claim, tier, settlement_mode)
    return _order_delay_due(claim, order)


def _marker_due(claim: OrderClaim, order: Order) -> datetime | None:
    """Resolve a post-033 claim from its explicit settlement marker."""
    if claim.settlement_mode_snapshot == SETTLEMENT_MODE_ORDER_DELAY:
        return _order_delay_due(claim, order)
    return _snapshot_settle_due(claim)


def _uses_legacy_tier(
    claim: OrderClaim, *, deposit_enabled: bool, tier: DepositTier | None
) -> bool:
    """Whether an old claim's due time was supplied by the active tier."""
    return claim.settlement_mode_snapshot is None and deposit_enabled and tier is not None


async def scan_due_payouts(
    db: AsyncSession, *, now: datetime | None = None
) -> list[int]:
    """扫描并自动结算到账时效到期的交付名额。

    每个名额独立 savepoint 结算（全额、deduction=0，走与 review_claim 相同的
    结算函数），失败只 log 不影响其他名额；结算成功后给打手发到账通知。
    """
    now = _as_utc(now or datetime.now(timezone.utc))

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
    candidates = await db.execute(query.order_by(OrderClaim.id.asc()))
    rows = candidates.all()
    balances = {}
    if deposit_on and rows:
        balance_result = await db.execute(
            select(Wallet.user_id, Wallet.deposit_balance).where(
                Wallet.user_id.in_({claim.booster_id for claim, _ in rows})
            )
        )
        balances = {
            user_id: Decimal(str(balance or 0))
            for user_id, balance in balance_result.all()
        }

    settled_claim_ids: list[int] = []
    for claim, order in rows:
        candidate_tier = (
            deposit_service.resolve_tier(
                tiers, balances.get(claim.booster_id, Decimal("0"))
            )
            if deposit_on
            else None
        )
        candidate_due = (
            _marker_due(claim, order)
            if claim.settlement_mode_snapshot is not None
            else _legacy_due(
                claim,
                order,
                deposit_enabled=deposit_on,
                tier=candidate_tier,
                settlement_mode=settlement_mode,
            )
        )
        if candidate_due is None:
            if claim.settlement_mode_snapshot is None:
                logger.info(
                    "Skipping legacy claim %s: no safe settlement due time",
                    claim.id,
                )
            continue
        if candidate_due > now:
            continue

        try:
            async with db.begin_nested():
                # Candidate rows can become delivered/settled or have their
                # immutable due time changed after the initial scan. Lock both
                # rows, then recompute the legacy mode/tier and due time from
                # current database state before settling.
                locked_order = (
                    await db.execute(
                        select(Order).where(Order.id == order.id).with_for_update()
                    )
                ).scalar_one_or_none()
                locked_claim = (
                    await db.execute(
                        select(OrderClaim)
                        .where(OrderClaim.id == claim.id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if locked_order is None or locked_claim is None:
                    continue
                if locked_claim.status != ClaimLifecycleStatus.DELIVERED:
                    continue

                locked_deposit_on = await deposit_service.is_deposit_enabled(db)
                locked_mode = SETTLEMENT_MODE_AFTER_DELIVERY
                locked_tier = None
                if locked_deposit_on:
                    locked_setting = await deposit_service.get_or_create_deposit_setting(db)
                    locked_mode = locked_setting.settlement_mode
                    locked_tier, _ = await deposit_service.get_user_tier(
                        db, locked_claim.booster_id
                    )
                locked_due = (
                    _marker_due(locked_claim, locked_order)
                    if locked_claim.settlement_mode_snapshot is not None
                    else _legacy_due(
                        locked_claim,
                        locked_order,
                        deposit_enabled=locked_deposit_on,
                        tier=locked_tier,
                        settlement_mode=locked_mode,
                    )
                )
                if locked_due is None or locked_due > now:
                    continue

                order_service = get_order_service(db)
                done = await order_service.auto_settle_due_claim(
                    locked_order,
                    locked_claim,
                    delay_from_tier=(
                        locked_claim.settlement_mode_snapshot is None
                        and locked_deposit_on
                        and locked_tier is not None
                    ),
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
