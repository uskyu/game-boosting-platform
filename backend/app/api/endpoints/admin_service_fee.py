"""后台「服务费设置」：全局服务费费率读取与保存。

发布订单时「服务费」开关开启且未手输费率，订单按发布瞬间的全局费率
烙盘（orders.service_fee_rate）；之后调整全局费率只影响新发的订单。
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
    description="返回当前全局服务费费率（百分数）。",
)
async def get_service_fee_settings(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> ServiceFeeSettingsResponse:
    setting = await service_fee_service.get_or_create_service_fee_setting(db)
    return ServiceFeeSettingsResponse(
        service_fee_rate=setting.service_fee_rate,
        updated_at=setting.updated_at,
    )


@router.put(
    "/settings",
    response_model=ServiceFeeSettingsResponse,
    summary="保存全局服务费设置",
    description="保存全局服务费费率；已发布订单不受影响，只对之后发布的订单生效。",
)
async def update_service_fee_settings(
    payload: ServiceFeeSettingsUpdate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> ServiceFeeSettingsResponse:
    setting = await service_fee_service.get_or_create_service_fee_setting(db)
    setting.service_fee_rate = Decimal(str(payload.service_fee_rate)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    setting.updated_by = current_admin.id
    await db.flush()
    await db.refresh(setting)
    return ServiceFeeSettingsResponse(
        service_fee_rate=setting.service_fee_rate,
        updated_at=setting.updated_at,
    )
