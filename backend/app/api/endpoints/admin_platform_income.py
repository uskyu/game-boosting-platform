"""后台「平台收入」：订单服务费的累计/月度统计与分页明细。

平台服务费不是任何用户的钱包余额，只写 ``platform_income``；本路由仅
管理员可访问，前台接口不得暴露这些数字。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import DatabaseSession, get_current_admin
from app.models.user import User
from app.schemas.serializers import serialize_datetime_utc
from app.services import platform_income_service

router = APIRouter(prefix="/admin/platform-income", tags=["admin-platform-income"])


class PlatformIncomeItem(BaseModel):
    """一条平台服务费收入流水。"""

    id: int = Field(description="流水ID")
    order_id: int = Field(description="订单ID")
    order_title: str | None = Field(default=None, description="订单标题")
    game_name: str | None = Field(default=None, description="游戏名")
    booster_id: int | None = Field(default=None, description="打手用户ID")
    booster_name: str | None = Field(default=None, description="打手用户名")
    amount: float = Field(description="服务费金额")
    service_fee_rate: float | None = Field(
        default=None, description="服务费费率（百分数，8.0 表示 8%）"
    )
    source: str = Field(description="收入来源")
    created_at: str | None = Field(default=None, description="入账时间(UTC)")


class PlatformIncomeListResponse(BaseModel):
    """平台服务费分页明细 + 汇总。"""

    items: list[PlatformIncomeItem] = Field(description="服务费流水")
    total: int = Field(description="当前筛选条件下的总条数")
    page: int = Field(description="页码")
    page_size: int = Field(description="每页条数")
    service_fee_total: float = Field(description="平台服务费累计收入")
    service_fee_month: float = Field(description="平台服务费本月收入（北京时间）")


@router.get(
    "",
    response_model=PlatformIncomeListResponse,
    summary="平台服务费明细",
    description="管理员查看订单服务费收入流水，可按 ?month=YYYY-MM 过滤北京自然月。",
)
async def list_platform_income(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    month: str | None = Query(
        default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="北京自然月 YYYY-MM"
    ),
) -> PlatformIncomeListResponse:
    try:
        rows, total = await platform_income_service.list_income(
            db, page=page, page_size=page_size, month=month
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    service_fee_total, service_fee_month = await platform_income_service.totals(db)

    items = [
        PlatformIncomeItem(
            id=record.id,
            order_id=record.order_id,
            order_title=order_title,
            game_name=game_name,
            booster_id=record.booster_id,
            booster_name=booster_name,
            amount=float(record.amount),
            service_fee_rate=(
                float(record.service_fee_rate)
                if record.service_fee_rate is not None
                else None
            ),
            source=record.source,
            created_at=serialize_datetime_utc(record.created_at),
        )
        for record, order_title, game_name, booster_name in rows
    ]

    return PlatformIncomeListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        service_fee_total=float(service_fee_total),
        service_fee_month=float(service_fee_month),
    )
