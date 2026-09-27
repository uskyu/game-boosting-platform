"""后台「提现机会刷新规则」：提现机会多久恢复一次读取与保存。

规则语义：
- INTERVAL：按每个用户自己滚动计时，每次提现成功后过 N 小时恢复 1 次
  机会；从没提过过的用户随时可提。
- DAILY_NOON：全站统一墙钟时间，窗口是 [今天 12:00, 明天 12:00)
  （产品时区 Asia/Shanghai），窗口内提过就没机会，到下一个 12:00
  全站恢复。

两种模式下每个刷新周期内每个用户只有 1 次提现机会；被驳回（REJECTED）
的申请不占机会。

修改后立即生效，正在等待中的用户按新规则重新计算；已发生的提现记录
本身不受影响。
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DatabaseSession, get_current_admin
from app.models.user import User
from app.schemas.withdrawal_rule import (
    WithdrawalRuleSettingsResponse,
    WithdrawalRuleSettingsUpdate,
)
from app.services.withdrawal_rule_service import get_or_create_withdrawal_rule_setting

router = APIRouter(prefix="/admin/withdrawal-rule", tags=["admin-withdrawal-rule"])


@router.get(
    "/settings",
    response_model=WithdrawalRuleSettingsResponse,
    summary="获取提现机会刷新规则",
    description="返回当前提现机会刷新模式与间隔。",
)
async def get_withdrawal_rule_settings(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> WithdrawalRuleSettingsResponse:
    setting = await get_or_create_withdrawal_rule_setting(db)
    return WithdrawalRuleSettingsResponse(
        mode=setting.mode,
        interval_hours=setting.interval_hours,
        updated_at=setting.updated_at,
    )


@router.put(
    "/settings",
    response_model=WithdrawalRuleSettingsResponse,
    summary="保存提现机会刷新规则",
    description=(
        "保存提现机会刷新规则；修改后立即生效，正在等待中的用户按新规则重新计算；"
        "被驳回的申请不占机会。"
    ),
)
async def update_withdrawal_rule_settings(
    payload: WithdrawalRuleSettingsUpdate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> WithdrawalRuleSettingsResponse:
    setting = await get_or_create_withdrawal_rule_setting(db)
    setting.mode = payload.mode
    setting.interval_hours = payload.interval_hours
    setting.updated_by = current_admin.id
    await db.flush()
    await db.refresh(setting)
    return WithdrawalRuleSettingsResponse(
        mode=setting.mode,
        interval_hours=setting.interval_hours,
        updated_at=setting.updated_at,
    )
