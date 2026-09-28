"""后台「提现渠道开关」：支付宝/微信是否对用户开放的读取与保存。

- 关闭某渠道后，前端不再显示该选项，后端对新提现申请校验渠道开关
  （进行中/已发放的旧提现不受影响）。
- 修改后立即生效、不烙盘：每次请求现读设置现算。
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DatabaseSession, get_current_admin
from app.models.user import User
from app.schemas.withdrawal_channel import (
    WithdrawalChannelSettingsResponse,
    WithdrawalChannelSettingsUpdate,
)
from app.services.withdrawal_channel_service import (
    get_or_create_withdrawal_channel_settings,
)

router = APIRouter(prefix="/admin/withdrawal-category", tags=["admin-withdrawal-category"])


@router.get(
    "/settings",
    response_model=WithdrawalChannelSettingsResponse,
    summary="获取提现渠道开关",
    description="返回各提现渠道（支付宝/微信）当前是否对用户开放。",
)
async def get_withdrawal_channel_settings(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> WithdrawalChannelSettingsResponse:
    setting = await get_or_create_withdrawal_channel_settings(db)
    return WithdrawalChannelSettingsResponse(
        alipay_enabled=setting.alipay_enabled,
        wechat_enabled=setting.wechat_enabled,
        updated_by=setting.updated_by,
        updated_at=setting.updated_at,
    )


@router.put(
    "/settings",
    response_model=WithdrawalChannelSettingsResponse,
    summary="保存提现渠道开关",
    description=(
        "保存提现渠道开关；至少修改一个开关，修改后立即生效（进行中/已发放的旧提现不受影响）。"
    ),
)
async def update_withdrawal_channel_settings(
    payload: WithdrawalChannelSettingsUpdate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> WithdrawalChannelSettingsResponse:
    setting = await get_or_create_withdrawal_channel_settings(db)
    if payload.alipay_enabled is not None:
        setting.alipay_enabled = payload.alipay_enabled
    if payload.wechat_enabled is not None:
        setting.wechat_enabled = payload.wechat_enabled
    setting.updated_by = current_admin.id
    await db.flush()
    await db.refresh(setting)
    return WithdrawalChannelSettingsResponse(
        alipay_enabled=setting.alipay_enabled,
        wechat_enabled=setting.wechat_enabled,
        updated_by=setting.updated_by,
        updated_at=setting.updated_at,
    )
