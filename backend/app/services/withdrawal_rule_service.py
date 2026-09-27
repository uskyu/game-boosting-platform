"""提现机会刷新规则（单行读取，缺失时按默认规则创建）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.withdrawal_rule import WithdrawalRefreshMode, WithdrawalRuleSetting

__all__ = [
    "DEFAULT_INTERVAL_HOURS",
    "DEFAULT_MODE",
    "get_or_create_withdrawal_rule_setting",
]

# 默认规则：模式 A，每隔 24 小时刷新一次。
DEFAULT_MODE = WithdrawalRefreshMode.INTERVAL
DEFAULT_INTERVAL_HOURS = 24


async def get_or_create_withdrawal_rule_setting(db: AsyncSession) -> WithdrawalRuleSetting:
    """读取提现机会刷新规则（单行，id=1）；缺失时按默认规则创建。"""
    result = await db.execute(
        select(WithdrawalRuleSetting).where(WithdrawalRuleSetting.id == 1)
    )
    setting = result.scalar_one_or_none()
    if setting is None:
        setting = WithdrawalRuleSetting(
            id=1,
            mode=DEFAULT_MODE,
            interval_hours=DEFAULT_INTERVAL_HOURS,
        )
        db.add(setting)
        await db.flush()
        await db.refresh(setting)
    return setting
