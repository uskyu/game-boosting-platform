"""保证金业务逻辑。

- 阶梯配置读取与默认值补齐（后台可增删改）；
- 按当前保证金余额解析命中的档位（门槛 ≤ 余额 的最高一档）；
- 余额 ⇄ 保证金 划转，含「最后一单完成后 7 天才能转回」的冷却期校验。

真正的余额变动全部在 ``WalletService``，本模块只做规则判定与编排。
"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.deposit import (
    DEFAULT_DEPOSIT_RETURN_COOLDOWN_DAYS,
    DEFAULT_DEPOSIT_TIERS,
    SETTLEMENT_MODE_AFTER_DELIVERY,
    SETTLEMENT_MODES,
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


async def get_user_tier(db: AsyncSession, user_id: int) -> tuple[DepositTier | None, Decimal]:
    """返回 (命中档位, 当前保证金余额)。"""
    wallet_service = get_wallet_service(db)
    wallet = await wallet_service.get_or_create_wallet(user_id)
    tiers = await list_tiers(db)
    balance = Decimal(str(wallet.deposit_balance or 0))
    return resolve_tier(tiers, balance), balance


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
    """缴纳保证金：余额 → 保证金。总开关关闭时拒绝缴纳。"""
    if not await is_deposit_enabled(db):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="保证金功能未开启",
        )
    amount = Decimal(str(amount)).quantize(_CENT, rounding=ROUND_HALF_UP)
    wallet_service = get_wallet_service(db)
    wallet = await wallet_service.get_or_create_wallet(user.id)
    await wallet_service.transfer_to_deposit(wallet, amount=amount)


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
    "ensure_default_tiers",
    "get_or_create_deposit_setting",
    "get_user_tier",
    "is_deposit_enabled",
    "list_tiers",
    "resolve_tier",
    "return_block_reason",
    "transfer_in",
    "transfer_out",
]
