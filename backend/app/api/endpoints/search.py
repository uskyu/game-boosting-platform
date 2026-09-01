"""Unified search API endpoints."""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import String, Text, cast, func, or_, select

from app.api.deps import DatabaseSession
from app.core.security import escape_like
from app.models.booster_service import BoosterService
from app.models.game import Game, GameCategory, GamePlatform
from app.models.order import Order, OrderStatus
from app.schemas.booster_service import BoosterServiceListResponse, BoosterServiceResponse
from app.schemas.order import OrderListResponse, OrderResponse
from app.schemas.search import SearchResponse, SearchType

router = APIRouter(prefix="/search", tags=["search"])


def _build_order_response(
    items: list[Order],
    total: int,
    page: int,
    page_size: int,
) -> OrderListResponse:
    pages = (total + page_size - 1) // page_size if total > 0 else 0
    # Search is public (no auth). Never expose game_account to anonymous
    # viewers — only the owner, assigned booster, or admin may see it
    # via the authenticated /orders endpoints.
    def _to_public(item: Order) -> OrderResponse:
        response = OrderResponse.model_validate(item)
        return response.model_copy(update={"game_account": None})

    return OrderListResponse(
        items=[_to_public(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


def _build_service_response(
    items: list[BoosterService],
    total: int,
    page: int,
    page_size: int,
) -> BoosterServiceListResponse:
    pages = (total + page_size - 1) // page_size if total > 0 else 0
    return BoosterServiceListResponse(
        items=[BoosterServiceResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get("/", response_model=SearchResponse)
async def search(
    db: DatabaseSession,
    q: Annotated[str, Query(description="关键词")] = "",
    type: Annotated[SearchType, Query(description="搜索类型")] = SearchType.ALL,
    game_id: Annotated[int | None, Query(description="游戏ID")] = None,
    category: Annotated[GameCategory | None, Query(description="游戏分类")] = None,
    platform: Annotated[GamePlatform | None, Query(description="游戏平台")] = None,
    price_min: Annotated[Decimal | None, Query(description="最低价格")] = None,
    price_max: Annotated[Decimal | None, Query(description="最高价格")] = None,
    service_type: Annotated[str | None, Query(description="服务类型")] = None,
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 20,
) -> SearchResponse:
    keyword = q.strip()
    like_pattern = f"%{escape_like(keyword)}%"

    order_results: OrderListResponse | None = None
    service_results: BoosterServiceListResponse | None = None

    if type in (SearchType.ORDERS, SearchType.ALL):
        order_filters = [Order.status == OrderStatus.PENDING]
        order_stmt = select(Order)
        order_count_stmt = select(func.count(Order.id))

        if keyword:
            keyword_filter = or_(
                Order.game_name.like(like_pattern),
                Order.description_raw.like(like_pattern),
                cast(Order.ai_tags, Text).like(like_pattern),
            )
            order_filters.append(keyword_filter)

        if game_id is not None:
            order_filters.append(Order.game_id == game_id)
        if price_min is not None:
            order_filters.append(Order.price >= price_min)
        if price_max is not None:
            order_filters.append(Order.price <= price_max)
        if service_type:
            order_filters.append(Order.service_type == service_type.strip())
        if category is not None or platform is not None:
            order_stmt = order_stmt.join(Game, Order.game_id == Game.id)
            order_count_stmt = order_count_stmt.join(Game, Order.game_id == Game.id)
            if category is not None:
                order_filters.append(Game.category == category)
            if platform is not None:
                order_filters.append(Game.platform == platform)

        order_total = int(
            (await db.execute(order_count_stmt.where(*order_filters))).scalar() or 0
        )
        orders = list(
            (
                await db.execute(
                    order_stmt
                    .where(*order_filters)
                    .order_by(Order.created_at.desc(), Order.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
        )
        order_results = _build_order_response(orders, order_total, page, page_size)

    if type in (SearchType.SERVICES, SearchType.ALL):
        service_filters = [BoosterService.is_available.is_(True)]
        service_stmt = select(BoosterService)
        service_count_stmt = select(func.count(BoosterService.id))

        if keyword:
            keyword_filter = or_(
                BoosterService.title.like(like_pattern),
                BoosterService.description.like(like_pattern),
                cast(BoosterService.tags, String(2000)).like(like_pattern),
            )
            service_filters.append(keyword_filter)

        if game_id is not None:
            service_filters.append(BoosterService.game_id == game_id)
        if price_min is not None:
            service_filters.append(BoosterService.price_per_hour >= price_min)
        if price_max is not None:
            service_filters.append(BoosterService.price_per_hour <= price_max)
        if service_type:
            service_filters.append(BoosterService.service_type == service_type.strip())
        if category is not None or platform is not None:
            service_stmt = service_stmt.join(Game, BoosterService.game_id == Game.id)
            service_count_stmt = service_count_stmt.join(Game, BoosterService.game_id == Game.id)
            if category is not None:
                service_filters.append(Game.category == category)
            if platform is not None:
                service_filters.append(Game.platform == platform)

        service_total = int(
            (await db.execute(service_count_stmt.where(*service_filters))).scalar() or 0
        )
        services = list(
            (
                await db.execute(
                    service_stmt
                    .where(*service_filters)
                    .order_by(BoosterService.created_at.desc(), BoosterService.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
        )
        service_results = _build_service_response(services, service_total, page, page_size)

    return SearchResponse(
        q=keyword,
        type=type,
        orders=order_results,
        services=service_results,
    )
