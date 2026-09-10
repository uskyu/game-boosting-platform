"""管理员易支付配置接口。

商户密钥只以 ``has_key`` 暴露「是否已配置」，永远不会原样下发前端。
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DatabaseSession, get_current_admin
from app.models.payment_setting import PaymentSetting
from app.models.user import User
from app.schemas.recharge import PaymentSettingResponse, PaymentSettingUpdate
from app.services import recharge_service

router = APIRouter(prefix="/admin/payment", tags=["admin-payment"])


def _to_response(setting: PaymentSetting) -> PaymentSettingResponse:
    return PaymentSettingResponse(
        enabled=setting.enabled,
        pay_address=setting.pay_address,
        epay_id=setting.epay_id,
        has_key=bool(setting.epay_key),
        alipay_enabled=setting.alipay_enabled,
        wxpay_enabled=setting.wxpay_enabled,
        min_amount=setting.min_amount,
        updated_at=setting.updated_at,
    )


@router.get(
    "/settings",
    response_model=PaymentSettingResponse,
    summary="获取支付配置",
    description="读取易支付配置；商户密钥仅以 has_key 表示是否已配置。",
)
async def get_payment_settings(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> PaymentSettingResponse:
    setting = await recharge_service.get_or_create_payment_setting(db)
    return _to_response(setting)


@router.put(
    "/settings",
    response_model=PaymentSettingResponse,
    summary="保存支付配置",
    description="保存易支付接口地址、商户ID、商户密钥、启用的支付方式与最低充值金额。epay_key 留空表示不修改。回调地址无需配置，按充值请求来源自动推导。",
)
async def update_payment_settings(
    payload: PaymentSettingUpdate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> PaymentSettingResponse:
    setting = await recharge_service.get_or_create_payment_setting(db)

    setting.enabled = payload.enabled
    setting.pay_address = payload.pay_address
    setting.epay_id = payload.epay_id
    setting.alipay_enabled = payload.alipay_enabled
    setting.wxpay_enabled = payload.wxpay_enabled
    setting.min_amount = Decimal(str(payload.min_amount)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    # 密钥留空 = 保持原值；只有明确传了新值才覆盖
    if payload.epay_key is not None:
        setting.epay_key = payload.epay_key

    setting.updated_by = current_admin.id
    await db.flush()
    await db.refresh(setting)
    return _to_response(setting)
