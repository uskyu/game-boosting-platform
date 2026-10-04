"""
订单大厅（Hall）辅助端点。

「今日已接单」区块数据源。登录即可访问（与大厅订单列表一致：
GET /orders 本身就需要登录）。

注意：本路由前缀与 orders.py 相同（/orders），且路径 /recent-claims
会被 /orders/{order_id}（int 路径参数）的模式抢先匹配成 422，因此
api_router 必须把 order_hall_router 注册在 orders_router 之前
（与 orders.py 内部 /claims/mine 先于 /{order_id} 定义同理）。
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DatabaseSession
from app.schemas.hall import RecentClaimItem, RecentClaimsResponse
from app.services.recent_claims_service import get_recent_claims_service

router = APIRouter(prefix="/orders", tags=["订单"])


@router.get(
    "/recent-claims",
    response_model=RecentClaimsResponse,
    summary="今日已接单",
    description="大厅「今日已接单」区块：今天已被抢的订单，按接单时间倒序",
)
async def list_recent_claims(
    current_user: CurrentUser,
    db: DatabaseSession,
    scope: Annotated[
        Literal["today"],
        Query(description="时间范围，当前仅支持 today"),
    ] = "today",
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="每页数量"),
    ] = 20,
    offset: Annotated[
        int,
        Query(ge=0, description="偏移量（加载更多用）"),
    ] = 0,
    page: Annotated[
        int | None,
        Query(ge=1, description="页码（与 offset 二选一，换算 offset=(page-1)*limit）"),
    ] = None,
) -> RecentClaimsResponse:
    """
    今日已接单列表（大厅区块）。

    - 需要登录；scope=today 按接单时间（OrderClaim.created_at）过滤今天
    - 按 claim id 倒序（最新接单在前）；limit 上限 100
    - page 与 offset 均可翻页：传 page 时 offset=(page-1)*limit
    - total 为今天的总接单数（不受 limit 影响），供区块头部计数
    """
    effective_offset = offset
    if page is not None:
        effective_offset = (page - 1) * limit

    service = get_recent_claims_service(db)
    rows, total = await service.list_recent_claims(
        scope=scope,
        limit=limit,
        offset=effective_offset,
    )

    return RecentClaimsResponse(
        total=total,
        items=[RecentClaimItem.model_validate(row) for row in rows],
    )
