"""保证金业务逻辑。

- 阶梯配置读取与默认值补齐（后台可增删改）；
- 按当前保证金余额解析命中的档位（门槛 ≤ 余额 的最高一档）；
- 余额 ⇄ 保证金 划转，含「最后一单完成后 7 天才能转回」的冷却期校验。

真正的余额变动全部在 ``WalletService``，本模块只做规则判定与编排。
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.product_time import product_day_bounds
from app.models.deposit import (
    DEFAULT_DEPOSIT_RETURN_COOLDOWN_DAYS,
    DEFAULT_DEPOSIT_TIERS,
    SETTLEMENT_MODE_AFTER_APPROVAL,
    SETTLEMENT_MODE_AFTER_DELIVERY,
    SETTLEMENT_MODE_ORDER_DELAY,
    DepositSetting,
    DepositTier,
)
from app.models.order import ClaimLifecycleStatus, OrderClaim
from app.models.user import User
from app.services.wallet_service import get_wallet_service

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")
_UNFINISHED_CLAIM_STATES = (
    ClaimLifecycleStatus.CLAIMED,
    ClaimLifecycleStatus.DELIVERED,
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def get_or_create_deposit_setting(db: AsyncSession) -> DepositSetting:
    """读取保证金总开关（单行），缺失时以「关闭 + 默认冷却天数」创建。"""
    result = await db.execute(select(DepositSetting).where(DepositSetting.id == 1))
    setting = result.scalar_one_or_none()
    if setting is None:
        setting = DepositSetting(
            id=1,
            enabled=False,
            return_cooldown_days=DEFAULT_DEPOSIT_RETURN_COOLDOWN_DAYS,
            default_compensation=Decimal("20.00"),
            settlement_mode=SETTLEMENT_MODE_AFTER_DELIVERY,
            global_booster_quota=5,
        )
        db.add(setting)
        await db.flush()
        await db.refresh(setting)
    return setting


async def is_deposit_enabled(db: AsyncSession) -> bool:
    """保证金玩法是否已开启（后台「保证金管理」总开关）。"""
    setting = await get_or_create_deposit_setting(db)
    return bool(setting.enabled)


async def ensure_default_tiers(db: AsyncSession) -> None:
    """阶梯表为空时写入默认阶梯（新建库 / 测试库自动补齐）。"""
    existing = await db.execute(select(func.count(DepositTier.id)))
    if int(existing.scalar() or 0) > 0:
        return
    for row in DEFAULT_DEPOSIT_TIERS:
        db.add(DepositTier(**row, enabled=True))
    await db.flush()
    logger.info("Seeded %s default deposit tiers", len(DEFAULT_DEPOSIT_TIERS))


async def list_tiers(db: AsyncSession, *, enabled_only: bool = True) -> list[DepositTier]:
    """按门槛升序返回阶梯。"""
    await ensure_default_tiers(db)
    query = select(DepositTier).order_by(DepositTier.threshold.asc())
    if enabled_only:
        query = query.where(DepositTier.enabled.is_(True))
    result = await db.execute(query)
    return list(result.scalars().all())


def resolve_tier(tiers: list[DepositTier], deposit_balance: Decimal) -> DepositTier | None:
    """命中档位 = 门槛 ≤ 保证金余额 的最高一档；都不满足时取最低一档。"""
    if not tiers:
        return None
    balance = Decimal(str(deposit_balance or 0))
    matched: DepositTier | None = None
    for tier in tiers:  # 已按门槛升序
        if Decimal(str(tier.threshold)) <= balance:
            matched = tier
        else:
            break
    return matched or tiers[0]


_SNAPSHOT_SETTLEMENT_MODES = (
    SETTLEMENT_MODE_AFTER_DELIVERY,
    SETTLEMENT_MODE_AFTER_APPROVAL,
)


def _configured_settlement_mode(setting: DepositSetting) -> str:
    """Return a safe settlement mode for legacy claims."""
    if setting.settlement_mode in _SNAPSHOT_SETTLEMENT_MODES:
        return setting.settlement_mode
    return SETTLEMENT_MODE_ORDER_DELAY


def _claim_settlement_start(claim: OrderClaim, mode: str) -> datetime | None:
    """Return the timestamp used by a deposit settlement timer."""
    if mode == SETTLEMENT_MODE_AFTER_APPROVAL:
        return claim.approved_at
    if mode == SETTLEMENT_MODE_AFTER_DELIVERY:
        return claim.delivered_at
    return None


def resolve_unsettled_settlement_due(
    claim: OrderClaim,
    tier: DepositTier | None,
    setting: DepositSetting,
) -> datetime | None:
    """Resolve a claim's current due time without lengthening it.

    Valid snapshots keep their original mode and timer origin. The current
    tier may shorten the timer, while legacy/fallback snapshots are upgraded
    to the currently configured mode. This pure resolver is safe to use while
    the scheduler is deciding which rows need locking.
    """
    if tier is None:
        return None

    current_hours = max(int(tier.settle_hours or 0), 0)
    existing_mode = claim.settlement_mode_snapshot
    existing_hours = claim.settle_hours_snapshot

    if existing_mode in _SNAPSHOT_SETTLEMENT_MODES:
        if (
            existing_hours is not None
            and int(existing_hours) <= current_hours
            and claim.settlement_due_at is not None
        ):
            return _as_utc(claim.settlement_due_at)
        mode = existing_mode
        # A missing due marker is repaired at the old duration unless the
        # current tier is faster; never invent a longer timer during repair.
        effective_hours = current_hours
        if existing_hours is not None:
            effective_hours = min(effective_hours, max(int(existing_hours), 0))
    else:
        mode = _configured_settlement_mode(setting)
        effective_hours = current_hours

    started_at = _claim_settlement_start(claim, mode)
    if started_at is None:
        return None
    return _as_utc(started_at) + timedelta(hours=effective_hours)


def _refresh_claim_settlement_terms(
    claim: OrderClaim,
    tier: DepositTier,
    setting: DepositSetting,
) -> bool:
    """Shorten one unsettled claim to the current deposit tier if needed."""
    current_hours = max(int(tier.settle_hours or 0), 0)
    existing_mode = claim.settlement_mode_snapshot

    if existing_mode is None:
        mode = _configured_settlement_mode(setting)
        if mode == SETTLEMENT_MODE_ORDER_DELAY:
            return False

        new_due = resolve_unsettled_settlement_due(claim, tier, setting)
        changed = False
        if claim.settle_hours_snapshot != current_hours:
            claim.settle_hours_snapshot = current_hours
            changed = True
        if claim.settlement_due_at != new_due:
            claim.settlement_due_at = new_due
            changed = True
        return changed

    old_hours = claim.settle_hours_snapshot
    old_due = _as_utc(claim.settlement_due_at) if claim.settlement_due_at else None
    new_due = resolve_unsettled_settlement_due(claim, tier, setting)
    should_refresh = (
        old_hours is None
        or current_hours < int(old_hours)
        or (old_due is None and new_due is not None)
        or (new_due is not None and old_due is not None and new_due < old_due)
    )
    if not should_refresh:
        return False

    changed = False
    if old_hours != current_hours:
        claim.settle_hours_snapshot = current_hours
        changed = True
    if old_due != new_due:
        claim.settlement_due_at = new_due
        changed = True
    return changed


async def refresh_unsettled_claim_settlements(
    db: AsyncSession,
    user_id: int,
    *,
    claims: list[OrderClaim] | None = None,
) -> int:
    """Apply the current deposit tier to a user's unsettled claims.

    This only shortens existing timers. It also covers users who already had
    a high deposit balance before this behavior was deployed; reads and the
    scheduler can therefore catch up old unsettled claims without touching
    settled claims or approved payout terms.
    """
    if claims is None:
        result = await db.execute(
            select(OrderClaim)
            .where(
                OrderClaim.booster_id == user_id,
                OrderClaim.status.in_(_UNFINISHED_CLAIM_STATES),
                OrderClaim.settled_at.is_(None),
            )
            .with_for_update()
        )
        claims = list(result.scalars().all())
    if not claims:
        return 0

    setting = await get_or_create_deposit_setting(db)
    if not setting.enabled:
        return 0
    tier, _balance = await get_user_tier(db, user_id)
    if tier is None:
        return 0

    changed = sum(
        _refresh_claim_settlement_terms(claim, tier, setting)
        for claim in claims
        if claim.status in _UNFINISHED_CLAIM_STATES and claim.settled_at is None
    )
    if changed:
        await db.flush()
    return changed


async def get_user_tier(db: AsyncSession, user_id: int) -> tuple[DepositTier | None, Decimal]:
    """返回 (命中档位, 当前保证金余额)。"""
    wallet_service = get_wallet_service(db)
    wallet = await wallet_service.get_or_create_wallet(user_id)
    tiers = await list_tiers(db)
    balance = Decimal(str(wallet.deposit_balance or 0))
    return resolve_tier(tiers, balance), balance


# ---------------------------------------------------------------------------
# 每日接单量（按北京时间每日 0:00 刷新）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DailyAcceptStatus:
    """打手每日接单量现状（接单闸门与「查看权益」页共用同一口径）。

    next_refresh_at 是 naive UTC，与库里 DATETIME 列同一约定。
    """

    limit: int | None          # 当前档位每日接单量上限（None = 不限）
    accepted: int              # 今日窗口内已接且订单未取消的订单数
    next_refresh_at: datetime | None  # 下次刷新时间（窗口起点 + 1 天）
    level: int                 # 档位等级（生效档按门槛升序的 1-based 序号）
    label: str                 # 档位标签（弹窗「你的权益」）


def normalize_daily_accept_limit(value: int | None) -> int | None:
    """0 与 None 同义 = 不限，统一归一成 None 再写库/比较。"""
    if value is None:
        return None
    limit = int(value)
    return limit if limit > 0 else None


def daily_accept_window(now: datetime) -> tuple[datetime, datetime]:
    """每日接单量统计窗口，返回 (window_start, next_refresh)，均为 naive UTC。

    北京时间自然日 [今日 00:00, 次日 00:00)——凌晨 0 点刷新（老板 2026-10-17
    确认；注意与提现机会的「每天 12:00 刷新」不同口径，勿再"顺手统一"）。
    """
    return product_day_bounds(now)


async def daily_accept_count(
    db: AsyncSession, booster_id: int, window_start: datetime
) -> int:
    """窗口内接单且订单未取消的订单数（被取消的 claim 不计数）。"""
    result = await db.execute(
        select(func.count(OrderClaim.id)).where(
            OrderClaim.booster_id == booster_id,
            OrderClaim.created_at >= window_start,
            OrderClaim.status != ClaimLifecycleStatus.CANCELLED,
        )
    )
    return int(result.scalar() or 0)


def tier_level_and_label(
    tiers: list[DepositTier], tier: DepositTier | None
) -> tuple[int, str]:
    """档位等级与标签：等级 = 生效档按门槛升序的 1-based 序号。

    0 元档标签取「默认档」（对用户比「¥0 档」更直观），其余为
    「保证金 ¥{threshold:g} 档」（:g 去尾零，1000.00 → ¥1000）。
    """
    if tier is None:
        return 0, "默认档"
    level = 0
    for index, item in enumerate(tiers, start=1):  # 已按门槛升序
        if item.id == tier.id or item is tier:
            level = index
            break
    threshold = Decimal(str(tier.threshold or 0))
    # 去尾零展示：1000.00 → ¥1000，100.50 → ¥100.5（Decimal 的 :g 会保留 .00）
    threshold_text = f"{threshold:f}"
    if "." in threshold_text:
        threshold_text = threshold_text.rstrip("0").rstrip(".")
    label = "默认档" if threshold == 0 else f"保证金 ¥{threshold_text} 档"
    return level, label


async def daily_accept_status(db: AsyncSession, booster_id: int) -> DailyAcceptStatus:
    """汇总打手每日接单量现状：当前档位上限、窗口已接数、下次刷新时间。"""
    wallet_service = get_wallet_service(db)
    wallet = await wallet_service.get_or_create_wallet(booster_id)
    tiers = await list_tiers(db)
    tier = resolve_tier(tiers, wallet.deposit_balance)
    limit = normalize_daily_accept_limit(
        tier.daily_accept_limit if tier is not None else None
    )
    window_start, next_refresh = daily_accept_window(datetime.now(timezone.utc))
    accepted = await daily_accept_count(db, booster_id, window_start)
    level, label = tier_level_and_label(tiers, tier)
    return DailyAcceptStatus(
        limit=limit,
        accepted=accepted,
        next_refresh_at=next_refresh,
        level=level,
        label=label,
    )


async def return_block_reason(db: AsyncSession, user_id: int) -> str | None:
    """保证金转回余额的冷却期校验（None = 可转回）。

    规则：没有未完成的名额，且最后一个已结算名额距今满 N 天
    （N 由后台「保证金管理」配置，默认 7 天）。
    """
    setting = await get_or_create_deposit_setting(db)
    cooldown_days = int(
        setting.return_cooldown_days or DEFAULT_DEPOSIT_RETURN_COOLDOWN_DAYS
    )

    unfinished = await db.execute(
        select(func.count(OrderClaim.id)).where(
            OrderClaim.booster_id == user_id,
            OrderClaim.status.in_(_UNFINISHED_CLAIM_STATES),
        )
    )
    if int(unfinished.scalar() or 0) > 0:
        return "还有未完成的订单，请先完成全部订单"

    last_settled = await db.execute(
        select(func.max(OrderClaim.settled_at)).where(
            OrderClaim.booster_id == user_id,
            OrderClaim.status == ClaimLifecycleStatus.SETTLED,
        )
    )
    last_at = last_settled.scalar()
    if last_at is None:
        # 从未接单结算过，无冷却期
        return None

    due_at = _as_utc(last_at) + timedelta(days=cooldown_days)
    now = datetime.now(timezone.utc)
    if now < due_at:
        remaining = due_at - now
        return (
            f"全部订单完成后需满 {cooldown_days} 天才能转回余额，"
            f"还需等待 {remaining.days} 天 {remaining.seconds // 3600} 小时"
        )
    return None


async def transfer_in(db: AsyncSession, user: User, amount: Decimal) -> None:
    """缴纳保证金：余额 → 保证金。总开关关闭时拒绝缴纳。

    充值升级后，所有尚未结算的报名记录都要立即按新的保证金档位
    重算结算时效。先锁报名记录、再锁钱包，和结算调度器的锁顺序一致，
    避免充值与自动结算并发时互相等待。
    """
    if not await is_deposit_enabled(db):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="保证金功能未开启",
        )
    amount = Decimal(str(amount)).quantize(_CENT, rounding=ROUND_HALF_UP)
    wallet_service = get_wallet_service(db)

    # 结算调度器的顺序是 order -> claim -> wallet；这里先锁 claim，
    # 再进入钱包转账，保持一致，防止充值升级与自动结算形成死锁。
    unsettled_result = await db.execute(
        select(OrderClaim)
        .where(
            OrderClaim.booster_id == user.id,
            OrderClaim.status.in_(_UNFINISHED_CLAIM_STATES),
            OrderClaim.settled_at.is_(None),
        )
        .with_for_update()
    )
    unsettled_claims = list(unsettled_result.scalars().all())

    wallet = await wallet_service.get_or_create_wallet(user.id)
    await wallet_service.transfer_to_deposit(wallet, amount=amount)

    if not unsettled_claims:
        return

    await refresh_unsettled_claim_settlements(
        db,
        user.id,
        claims=unsettled_claims,
    )


async def transfer_out(db: AsyncSession, user: User, amount: Decimal) -> None:
    """转回余额：保证金 → 余额（受冷却期约束）。

    总开关关闭时依然允许转回，避免把用户已缴的钱锁死。
    """
    amount = Decimal(str(amount)).quantize(_CENT, rounding=ROUND_HALF_UP)

    reason = await return_block_reason(db, user.id)
    if reason is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=reason,
        )

    wallet_service = get_wallet_service(db)
    wallet = await wallet_service.get_or_create_wallet(user.id)
    await wallet_service.transfer_from_deposit(wallet, amount=amount)


__all__ = [
    "DailyAcceptStatus",
    "daily_accept_count",
    "daily_accept_status",
    "daily_accept_window",
    "ensure_default_tiers",
    "get_or_create_deposit_setting",
    "get_user_tier",
    "is_deposit_enabled",
    "list_tiers",
    "normalize_daily_accept_limit",
    "refresh_unsettled_claim_settlements",
    "resolve_tier",
    "resolve_unsettled_settlement_due",
    "return_block_reason",
    "tier_level_and_label",
    "transfer_in",
    "transfer_out",
]
