"""管理员发单数据统计与订单级明细。"""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DatabaseSession, get_current_admin
from app.models.user import User
from app.services.order_analytics_service import (
    get_order_analytics,
    get_order_analytics_details,
    get_publisher_lifetime_analytics,
)

router = APIRouter(prefix="/admin/order-analytics", tags=["admin-order-analytics"])
publisher_router = APIRouter(prefix="/orders/my-analytics", tags=["publisher-order-analytics"])

Period = Literal["today", "7d", "30d"]
Metric = Literal["issued", "issued_amount", "cancelled", "completed", "settled"]


class OrderAnalyticsSummary(BaseModel):
    period_start: date
    period_end: date
    issued_orders: int = Field(description="周期内发单数（按发单时间）")
    cancelled_orders: int = Field(description="周期内取消订单数（按取消事件时间）")
    completed_orders: int = Field(description="周期内完结订单数（按完结时间）")
    settled_slots: int = Field(description="周期内已结算名额数")
    issued_amount: float = Field(description="周期内当前未取消发单总额（单价 × 最大名额）")
    successful_amount: float = Field(description="周期内成功成交总额（按已结算名额）")


class OrderAnalyticsDaily(BaseModel):
    date: date
    issued_orders: int
    cancelled_orders: int
    completed_orders: int
    settled_slots: int
    issued_amount: float
    successful_amount: float


class OrderAnalyticsLifetime(BaseModel):
    issued_orders: int
    cancelled_orders: int
    completed_orders: int
    settled_slots: int
    issued_amount: float
    successful_amount: float


class OrderAnalyticsResponse(BaseModel):
    period: Period
    timezone: str
    period_start: date
    period_end: date
    summary: OrderAnalyticsSummary
    daily: list[OrderAnalyticsDaily]


class MyOrderAnalyticsResponse(OrderAnalyticsResponse):
    lifetime: OrderAnalyticsLifetime


class OrderAnalyticsDetailItem(BaseModel):
    order_id: int
    title: str
    game_name: str
    publisher: str
    created_at: str | None
    event_at: str | None
    status: str
    order_status: str
    unit_price: float
    max_claims: int
    settled_slots: int
    metric_slots: int
    metric_amount: float
    amount: float


class OrderAnalyticsDetailsResponse(BaseModel):
    items: list[OrderAnalyticsDetailItem]
    total: int
    page: int
    pages: int
    page_size: int


@router.get("", response_model=OrderAnalyticsResponse, summary="发单数据汇总")
async def read_order_analytics(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    period: Period = Query(default="today", description="统计周期：today、7d、30d"),
) -> OrderAnalyticsResponse:
    return OrderAnalyticsResponse(**await get_order_analytics(db, period))


@router.get(
    "/details",
    response_model=OrderAnalyticsDetailsResponse,
    summary="发单数据订单明细",
)
async def read_order_analytics_details(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    period: Period = Query(default="today"),
    date_filter: date = Query(alias="date", description="北京时间自然日 YYYY-MM-DD"),
    metric: Metric = Query(description="指标：issued、issued_amount、cancelled、completed、settled"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> OrderAnalyticsDetailsResponse:
    try:
        data = await get_order_analytics_details(
            db,
            period=period,
            day=date_filter,
            metric=metric,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return OrderAnalyticsDetailsResponse(**data)


@publisher_router.get("", response_model=MyOrderAnalyticsResponse, summary="我的发单数据")
async def read_my_order_analytics(
    db: DatabaseSession,
    current_user: CurrentUser,
    period: Period = Query(default="today", description="统计周期：today、7d、30d"),
) -> MyOrderAnalyticsResponse:
    data = await get_order_analytics(db, period, publisher_id=current_user.id)
    data["lifetime"] = await get_publisher_lifetime_analytics(db, publisher_id=current_user.id)
    return MyOrderAnalyticsResponse(**data)


@publisher_router.get(
    "/details",
    response_model=OrderAnalyticsDetailsResponse,
    summary="我的发单数据订单明细",
)
async def read_my_order_analytics_details(
    db: DatabaseSession,
    current_user: CurrentUser,
    period: Period = Query(default="today"),
    date_filter: date = Query(alias="date", description="北京时间自然日 YYYY-MM-DD"),
    metric: Metric = Query(description="指标：issued、issued_amount、cancelled、completed、settled"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> OrderAnalyticsDetailsResponse:
    try:
        data = await get_order_analytics_details(
            db,
            period=period,
            day=date_filter,
            metric=metric,
            page=page,
            page_size=page_size,
            publisher_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return OrderAnalyticsDetailsResponse(**data)
