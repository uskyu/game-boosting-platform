"""Booster service marketplace API endpoints."""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.chat_utils import send_order_system_message
from app.api.deps import CurrentUser, DatabaseSession, OptionalCurrentUser
from app.models.booster_service import BoosterService
from app.models.game import Game
from app.models.user import User, UserRole
from app.schemas.booster_service import (
    BoosterServiceCreate,
    BoosterServiceListResponse,
    BoosterServiceOrderCreate,
    BoosterServiceResponse,
    BoosterServiceUpdate,
)
from app.schemas.order import OrderResponse
from app.schemas.user import MessageResponse
from app.services.chat_service import get_chat_service
from app.services.order_service import get_order_service

router = APIRouter(prefix="/services", tags=["services"])


def _ensure_booster_user(current_user: User) -> None:
    if current_user.role != UserRole.BOOSTER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有代练可以发布和管理服务",
        )


def _validate_service_type(game: Game, service_type: str) -> None:
    allowed_service_types = game.service_template.get("service_types", [])
    if allowed_service_types and service_type not in allowed_service_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="服务类型不属于该游戏模板",
        )


async def _get_game_or_404(
    db: DatabaseSession,
    game_id: int,
    require_active: bool = True,
) -> Game:
    result = await db.execute(
        select(Game).where(Game.id == game_id)
    )
    game = result.scalar_one_or_none()
    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="游戏不存在",
        )
    if require_active and not game.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该游戏当前未上架",
        )
    return game


async def _get_service_or_404(
    db: DatabaseSession,
    service_id: int,
) -> BoosterService:
    result = await db.execute(
        select(BoosterService).where(BoosterService.id == service_id)
    )
    service = result.scalar_one_or_none()
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="服务不存在",
        )
    return service


def _build_service_list_response(
    services: list[BoosterService],
    total: int,
    page: int,
    page_size: int,
) -> BoosterServiceListResponse:
    pages = (total + page_size - 1) // page_size if total > 0 else 0
    return BoosterServiceListResponse(
        items=[BoosterServiceResponse.model_validate(service) for service in services],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.post("/create", response_model=BoosterServiceResponse, status_code=status.HTTP_201_CREATED)
async def create_service(
    payload: BoosterServiceCreate,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> BoosterServiceResponse:
    _ensure_booster_user(current_user)
    game = await _get_game_or_404(db, payload.game_id)
    _validate_service_type(game, payload.service_type)

    service = BoosterService(
        booster_id=current_user.id,
        **payload.model_dump(),
        is_available=True,
        order_count=0,
    )
    db.add(service)
    await db.flush()
    await db.refresh(service)
    return BoosterServiceResponse.model_validate(service)


@router.get("/", response_model=BoosterServiceListResponse)
async def list_services(
    db: DatabaseSession,
    game_id: Annotated[int | None, Query(description="按游戏筛选")] = None,
    service_type: Annotated[str | None, Query(description="按服务类型筛选")] = None,
    price_min: Annotated[Decimal | None, Query(description="最低小时价")] = None,
    price_max: Annotated[Decimal | None, Query(description="最高小时价")] = None,
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 20,
) -> BoosterServiceListResponse:
    filters = [BoosterService.is_available.is_(True)]
    if game_id is not None:
        filters.append(BoosterService.game_id == game_id)
    if service_type:
        filters.append(BoosterService.service_type == service_type.strip())
    if price_min is not None:
        filters.append(BoosterService.price_per_hour >= price_min)
    if price_max is not None:
        filters.append(BoosterService.price_per_hour <= price_max)

    total = int(
        (await db.execute(select(func.count(BoosterService.id)).where(*filters))).scalar() or 0
    )
    services = list(
        (
            await db.execute(
                select(BoosterService)
                .where(*filters)
                .order_by(BoosterService.created_at.desc(), BoosterService.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
    )
    return _build_service_list_response(services, total, page, page_size)


@router.get("/my", response_model=BoosterServiceListResponse)
async def list_my_services(
    current_user: CurrentUser,
    db: DatabaseSession,
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 20,
) -> BoosterServiceListResponse:
    _ensure_booster_user(current_user)
    filters = [BoosterService.booster_id == current_user.id]
    total = int(
        (await db.execute(select(func.count(BoosterService.id)).where(*filters))).scalar() or 0
    )
    services = list(
        (
            await db.execute(
                select(BoosterService)
                .where(*filters)
                .order_by(BoosterService.updated_at.desc(), BoosterService.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
    )
    return _build_service_list_response(services, total, page, page_size)


@router.post("/{service_id}/order", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def order_from_service(
    service_id: int,
    payload: BoosterServiceOrderCreate,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> OrderResponse:
    service = await _get_service_or_404(db, service_id)
    if not service.is_available:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该服务已下架",
        )

    order_service = get_order_service(db)
    order = await order_service.create_service_order(
        service=service,
        payload=payload,
        current_user=current_user,
    )

    chat_service = get_chat_service(db)
    await chat_service.create_or_get_conversation(
        current_user=current_user,
        target_user_id=service.booster_id,
        order_id=order.id,
    )
    await send_order_system_message(
        db=db,
        order_id=order.id,
        content="用户已从服务卡片发起订单",
        meta_json={
            "event": "service_order_created",
            "order_id": order.id,
            "service_id": service.id,
            "user_id": current_user.id,
            "booster_id": service.booster_id,
        },
    )

    return OrderResponse.model_validate(order)


@router.get("/{service_id}", response_model=BoosterServiceResponse)
async def get_service(
    service_id: int,
    db: DatabaseSession,
    current_user: OptionalCurrentUser,
) -> BoosterServiceResponse:
    service = await _get_service_or_404(db, service_id)
    can_view_unavailable = (
        current_user is not None
        and (current_user.role == UserRole.ADMIN or current_user.id == service.booster_id)
    )
    if not service.is_available and not can_view_unavailable:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="服务不存在",
        )
    return BoosterServiceResponse.model_validate(service)


@router.put("/{service_id}", response_model=BoosterServiceResponse)
async def update_service(
    service_id: int,
    payload: BoosterServiceUpdate,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> BoosterServiceResponse:
    _ensure_booster_user(current_user)
    service = await _get_service_or_404(db, service_id)
    if service.booster_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只能修改自己的服务",
        )

    update_data = payload.model_dump(exclude_unset=True)
    target_game_id = update_data.get("game_id", service.game_id)
    target_service_type = update_data.get("service_type", service.service_type)
    game = await _get_game_or_404(
        db,
        target_game_id,
        require_active=target_game_id != service.game_id,
    )
    _validate_service_type(game, target_service_type)

    for field, value in update_data.items():
        setattr(service, field, value)

    await db.flush()
    await db.refresh(service)
    return BoosterServiceResponse.model_validate(service)


@router.delete("/{service_id}", response_model=MessageResponse)
async def delete_service(
    service_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> MessageResponse:
    _ensure_booster_user(current_user)
    service = await _get_service_or_404(db, service_id)
    if service.booster_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只能下架自己的服务",
        )

    service.is_available = False
    await db.flush()
    return MessageResponse(message="服务已下架", success=True)
