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
        daily_accept_limit=tier.daily_accept_limit,
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
        global_booster_quota=setting.global_booster_quota,
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

    # 这里**故意不做**「阶梯必须含 0 元档」的校验（2026-10-18 撤除）。
    #
    # 曾经的实现要求存在启用中的 0 元档，理由是 resolve_tier() 会用
    # `or tiers[0]` 把 0 保证金打手兜底成最低一档，从而继承其权益。但该前提
    # 与老板的实际配置相反，硬拦会直接卡死后台保存：
    #   - 老板有意删掉了 0 元档——原始默认 0 元档是 exempt_compensation=False，
    #     会让 0 保证金打手接单时必须冻结炸单赔付金（余额 0 即 400 接不了单）；
    #     删掉后 0 保证金打手兜底到 100 档、继承免冻结，才接得上单。
    #   - 老板 2026-10-18 亦明确表态「0 保证金识别到 100 保证金那个问题不大」。
    # 因此「0 保证金兜底到最低档」是**有意的承重行为，不要当成 bug 修**：
    # 移除 0 元档就等于允许 0 保证金打手接单，属产品决策而非数据缺陷。

    setting = await deposit_service.get_or_create_deposit_setting(db)
    setting.enabled = payload.enabled
    setting.return_cooldown_days = payload.return_cooldown_days
    setting.settlement_mode = payload.settlement_mode
    setting.global_booster_quota = payload.global_booster_quota
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
                # None = 该档不承诺加速（按订单到账时效），原样落库
                settle_hours=item.settle_hours,
                # 0 与 None 同义 = 不限，统一归一成 None 再写库
                daily_accept_limit=deposit_service.normalize_daily_accept_limit(
                    item.daily_accept_limit
                ),
                enabled=item.enabled,
            )
        )
    await db.flush()
    await db.refresh(setting)
    return await _settings_response(db, setting)
