"""后台「保证金管理」：总开关、冷却天数与阶梯整表维护。"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select

from app.api.deps import DatabaseSession, get_current_admin
from app.models.deposit import DepositSetting, DepositTier
from app.models.user import User
from app.schemas.deposit import (
    DepositSettingsAdminResponse,
    DepositSettingsAdminUpdate,
    DepositTierAdminResponse,
)
from app.services import deposit_service

router = APIRouter(prefix="/admin/deposit", tags=["admin-deposit"])


def _tier_response(tier: DepositTier) -> DepositTierAdminResponse:
    return DepositTierAdminResponse(
        id=tier.id,
        threshold=tier.threshold,
        wait_seconds=tier.wait_seconds,
        exempt_compensation=tier.exempt_compensation,
        settle_hours=tier.settle_hours,
        enabled=tier.enabled,
        updated_at=tier.updated_at,
    )


async def _settings_response(db, setting: DepositSetting) -> DepositSettingsAdminResponse:
    tiers = await deposit_service.list_tiers(db, enabled_only=False)
    return DepositSettingsAdminResponse(
        enabled=setting.enabled,
        return_cooldown_days=setting.return_cooldown_days,
        default_compensation=setting.default_compensation,
        settlement_mode=setting.settlement_mode,
        updated_at=setting.updated_at,
        tiers=[_tier_response(t) for t in tiers],
    )


@router.get(
    "/settings",
    response_model=DepositSettingsAdminResponse,
    summary="获取保证金管理配置",
    description="返回保证金模式总开关、转回冷却天数与完整阶梯列表。",
)
async def get_deposit_settings(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> DepositSettingsAdminResponse:
    setting = await deposit_service.get_or_create_deposit_setting(db)
    return await _settings_response(db, setting)


@router.put(
    "/settings",
    response_model=DepositSettingsAdminResponse,
    summary="保存保证金管理配置",
    description="保存总开关、冷却天数，并以整表替换方式保存阶梯（门槛不可重复）。",
)
async def update_deposit_settings(
    payload: DepositSettingsAdminUpdate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> DepositSettingsAdminResponse:
    thresholds = [Decimal(str(t.threshold)).quantize(Decimal("0.01")) for t in payload.tiers]
    if len(set(thresholds)) != len(thresholds):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="保证金阶梯的门槛不能重复",
        )

    setting = await deposit_service.get_or_create_deposit_setting(db)
    setting.enabled = payload.enabled
    setting.return_cooldown_days = payload.return_cooldown_days
    setting.settlement_mode = payload.settlement_mode
    setting.default_compensation = Decimal(str(payload.default_compensation)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    setting.updated_by = current_admin.id
    await db.flush()

    # 整表替换：阶梯数量少、且由管理员一次提交完整列表，比逐条 diff 更不易出错
    await db.execute(delete(DepositTier))
    await db.flush()
    for item in payload.tiers:
        db.add(
            DepositTier(
                threshold=Decimal(str(item.threshold)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                ),
                wait_seconds=item.wait_seconds,
                exempt_compensation=item.exempt_compensation,
                settle_hours=item.settle_hours,
                enabled=item.enabled,
            )
        )
    await db.flush()
    await db.refresh(setting)
    return await _settings_response(db, setting)
