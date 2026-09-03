"""Game catalog API endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import delete as sql_delete, func, select

from app.api.deps import DatabaseSession, get_current_admin
from app.models.game import Game, GameCategory, GamePlatform
from app.models.user import User
from app.models.order import Order
from app.schemas.game import GameBulkAction, GameCreate, GameListResponse, GameResponse, GameUpdate
from app.schemas.user import MessageResponse
from app.services.file_service import save_image_upload

router = APIRouter(prefix="/games", tags=["games"])


async def _get_game_or_404(
    db: DatabaseSession,
    game_id: int,
    *,
    include_inactive: bool = False,
) -> Game:
    stmt = select(Game).where(Game.id == game_id)
    if not include_inactive:
        stmt = stmt.where(Game.is_active.is_(True))

    result = await db.execute(stmt)
    game = result.scalar_one_or_none()
    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="游戏不存在",
        )
    return game


@router.get("/", response_model=GameListResponse)
async def list_games(
    db: DatabaseSession,
    category: Annotated[GameCategory | None, Query(description="按分类筛选")] = None,
    platform: Annotated[GamePlatform | None, Query(description="按平台筛选")] = None,
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 100,
) -> GameListResponse:
    filters = [Game.is_active.is_(True)]
    if category is not None:
        filters.append(Game.category == category)
    if platform is not None:
        filters.append(Game.platform == platform)

    count_stmt = select(func.count()).select_from(Game).where(*filters)
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        select(Game)
        .where(*filters)
        .order_by(Game.sort_order.asc(), Game.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    games = result.scalars().all()
    pages = (total + page_size - 1) // page_size if total > 0 else 0

    return GameListResponse(
        items=[GameResponse.model_validate(game) for game in games],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.put("/{game_id}/logo", response_model=GameResponse)
async def upload_game_logo(
    game_id: int,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    logo: UploadFile = File(...),
) -> GameResponse:
    # 管理操作：需管理员token，允许操作下架游戏
    game = await _get_game_or_404(db, game_id, include_inactive=True)
    logo_url = await save_image_upload(logo, "games", max_size_bytes=10 * 1024 * 1024)
    game.logo_url = logo_url
    await db.flush()
    await db.refresh(game)
    return GameResponse.model_validate(game)


@router.delete("/{game_id}/logo", response_model=MessageResponse)
async def delete_game_logo(
    game_id: int,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> MessageResponse:
    # 管理操作：需管理员token，允许操作下架游戏
    game = await _get_game_or_404(db, game_id, include_inactive=True)
    game.logo_url = None
    await db.flush()
    return MessageResponse(message="游戏 Logo 已清除", success=True)


@router.post("/bulk-action", response_model=MessageResponse)
async def bulk_game_action(
    payload: GameBulkAction,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> MessageResponse:
    result = await db.execute(select(Game).where(Game.id.in_(payload.ids)))
    games = list(result.scalars().all())
    if len(games) != len(payload.ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="部分游戏不存在")
    if payload.action == "activate":
        for game in games:
            game.is_active = True
        message = "游戏已批量上架"
    elif payload.action == "deactivate":
        for game in games:
            game.is_active = False
        message = "游戏已批量下架"
    else:
        order_result = await db.execute(
            select(Order.game_id).where(Order.game_id.in_(payload.ids)).limit(1)
        )
        if order_result.first() is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="存在关联订单，无法删除游戏")
        await db.execute(sql_delete(Game).where(Game.id.in_(payload.ids)))
        message = "游戏已批量删除"
    await db.flush()
    return MessageResponse(message=message, success=True)


@router.get("/{game_id}", response_model=GameResponse)
async def get_game(
    game_id: int,
    db: DatabaseSession,
) -> GameResponse:
    game = await _get_game_or_404(db, game_id)
    return GameResponse.model_validate(game)


@router.post(
    "/",
    response_model=GameResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_game(
    payload: GameCreate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> GameResponse:
    game = Game(**payload.model_dump())
    db.add(game)
    await db.flush()
    await db.refresh(game)
    return GameResponse.model_validate(game)


@router.put("/{game_id}", response_model=GameResponse)
async def update_game(
    game_id: int,
    payload: GameUpdate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> GameResponse:
    # 管理操作：需管理员token，允许操作下架游戏
    game = await _get_game_or_404(db, game_id, include_inactive=True)
    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(game, field, value)

    await db.flush()
    await db.refresh(game)
    return GameResponse.model_validate(game)


@router.delete("/{game_id}", response_model=MessageResponse)
async def delete_game(
    game_id: int,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> MessageResponse:
    # 管理操作：需管理员token，允许操作下架游戏
    game = await _get_game_or_404(db, game_id, include_inactive=True)
    await db.delete(game)
    await db.flush()
    return MessageResponse(message="游戏已删除", success=True)
