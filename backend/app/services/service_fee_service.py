"""全局服务费设置（单行读取，缺失时以 0 费率创建）。"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.service_fee import ServiceFeeSetting

__all__ = ["get_or_create_service_fee_setting", "get_global_service_fee_rate"]


async def get_or_create_service_fee_setting(db: AsyncSession) -> ServiceFeeSetting:
    """读取全局服务费设置（单行，id=1）；缺失时以 0 费率创建。"""
    result = await db.execute(select(ServiceFeeSetting).where(ServiceFeeSetting.id == 1))
    setting = result.scalar_one_or_none()
    if setting is None:
        setting = ServiceFeeSetting(id=1, service_fee_rate=Decimal("0.00"))
        db.add(setting)
        await db.flush()
        await db.refresh(setting)
    return setting


async def get_global_service_fee_rate(db: AsyncSession) -> Decimal:
    """当前全局服务费费率（百分数，如 8.00 表示 8%）。"""
    setting = await get_or_create_service_fee_setting(db)
    return Decimal(str(setting.service_fee_rate))
