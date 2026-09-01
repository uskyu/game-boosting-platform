"""Game catalog API endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import DatabaseSession, OptionalCurrentUser, get_current_admin
from app.models.game import Game, GameCategory, GamePlatform
from app.models.user import User, UserRole
from app.schemas.game import GameCreate, GameListResponse, GameResponse, GameUpdate
from app.schemas.user import MessageResponse

router = APIRouter(prefix="/games", tags=["games"])


def _should_include_inactive(current_user: User | None) -> bool:
    return current_user is not None and current_user.role == UserRole.ADMIN


async def _get_game_or_404(
    db: DatabaseSession,
    game_id: int,
    current_user: User | None = None,
) -> Game:
    stmt = select(Game).where(Game.id == game_id)
    if not _should_include_inactive(current_user):
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
    current_user: OptionalCurrentUser,
    category: Annotated[GameCategory | None, Query(description="按分类筛选")] = None,
    platform: Annotated[GamePlatform | None, Query(description="按平台筛选")] = None,
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 100,
) -> GameListResponse:
    filters = []
    if category is not None:
        filters.append(Game.category == category)
    if platform is not None:
        filters.append(Game.platform == platform)
    if not _should_include_inactive(current_user):
        filters.append(Game.is_active.is_(True))

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


@router.get("/{game_id}", response_model=GameResponse)
async def get_game(
    game_id: int,
    db: DatabaseSession,
    current_user: OptionalCurrentUser,
) -> GameResponse:
    game = await _get_game_or_404(db, game_id, current_user)
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
    game = await _get_game_or_404(db, game_id, current_admin)
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
    game = await _get_game_or_404(db, game_id, current_admin)
    await db.delete(game)
    await db.flush()
    return MessageResponse(message="游戏已删除", success=True)
