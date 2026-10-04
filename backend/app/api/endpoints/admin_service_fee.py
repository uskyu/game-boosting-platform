"""后台「服务费设置」：全局费率与逐单设置开关的读取和保存。

全局费率和订单逐单覆盖都在发单时固定到订单上；之后调整全局设置
只影响新发订单，不改变已发布订单的服务费。
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DatabaseSession, get_current_admin
from app.models.user import User
from app.schemas.service_fee import ServiceFeeSettingsResponse, ServiceFeeSettingsUpdate
from app.services import service_fee_service

router = APIRouter(prefix="/admin/service-fee", tags=["admin-service-fee"])


@router.get(
    "/settings",
    response_model=ServiceFeeSettingsResponse,
    summary="获取全局服务费设置",
    description="返回当前全局服务费费率与订单逐单设置开关。",
)
async def get_service_fee_settings(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> ServiceFeeSettingsResponse:
    setting = await service_fee_service.get_or_create_service_fee_setting(db)
    return ServiceFeeSettingsResponse(
        service_fee_rate=setting.service_fee_rate,
        individual_service_fee_enabled=setting.individual_service_fee_enabled,
        updated_at=setting.updated_at,
    )


@router.put(
    "/settings",
    response_model=ServiceFeeSettingsResponse,
    summary="保存全局服务费设置",
    description="保存全局费率与逐单设置开关；已发布订单不受影响，只对之后发布的订单生效。",
)
async def update_service_fee_settings(
    payload: ServiceFeeSettingsUpdate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> ServiceFeeSettingsResponse:
    setting = await service_fee_service.get_or_create_service_fee_setting(db)
    if payload.service_fee_rate is not None:
        setting.service_fee_rate = Decimal(str(payload.service_fee_rate)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    if payload.individual_service_fee_enabled is not None:
        setting.individual_service_fee_enabled = payload.individual_service_fee_enabled
    setting.updated_by = current_admin.id
    await db.flush()
    await db.refresh(setting)
    return ServiceFeeSettingsResponse(
        service_fee_rate=setting.service_fee_rate,
        individual_service_fee_enabled=setting.individual_service_fee_enabled,
        updated_at=setting.updated_at,
    )
