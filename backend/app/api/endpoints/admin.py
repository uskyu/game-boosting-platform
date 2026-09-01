"""Administrator endpoints."""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update

from app.api.chat_utils import send_order_system_message
from app.api.deps import DatabaseSession, get_current_admin
from app.api.notification_utils import notify_user
from app.models.booster_service import BoosterService
from app.models.game import Game, GameCategory, GamePlatform
from app.models.notification import NotificationType
from app.models.order import Order, OrderStatus
from app.models.user import BoosterApplicationStatus, User
from app.models.withdrawal import WithdrawalStatus
from app.schemas.admin import (
    AdminOrderAssignRequest,
    AdminOrderInterventionRequest,
    AdminWalletAdjustRequest,
    AdminWithdrawalMarkPaidRequest,
    AdminWithdrawalReviewRequest,
    BoosterApplicationResponse,
    BoosterApplicationReviewRequest,
)
from app.schemas.dashboard import (
    BoosterRankingResponse,
    GameDistributionResponse,
    OrderTrendResponse,
    OverviewStats,
    UserGrowthResponse,
)
from app.schemas.game import GameCreate, GameListResponse, GameResponse, GameUpdate
from app.schemas.order import OrderListResponse, OrderResponse
from app.schemas.user import MessageResponse
from app.schemas.wallet import (
    AdminWithdrawalListResponse,
    AdminWithdrawalResponse,
    WalletResponse,
    WithdrawalListResponse,
    WithdrawalResponse,
)
from app.services.dashboard_service import get_dashboard_service
from app.services.order_service import get_order_service
from app.services.user_service import get_user_service
from app.services.wallet_service import get_wallet_service

router = APIRouter(prefix="/admin", tags=["admin"])


def _map_application_response(user: User) -> BoosterApplicationResponse:
    return BoosterApplicationResponse(
        user_id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        status=user.booster_application_status,
        game_name=user.booster_application_game,
        current_rank=user.booster_application_current_rank,
        target_rank=user.booster_application_target_rank,
        proof_url=user.booster_application_proof_url,
        note=user.booster_application_note,
        booster_quota=user.booster_quota,
        reviewed_by_admin_id=user.reviewed_by_admin_id,
        reviewed_at=user.reviewed_at,
        review_note=user.review_note,
    )


@router.get("/users/applications", response_model=list[BoosterApplicationResponse])
async def list_user_applications(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    status_filter: BoosterApplicationStatus | None = Query(default=None, alias="status"),
) -> list[BoosterApplicationResponse]:
    user_service = get_user_service(db)
    users = await user_service.list_booster_applications(status_filter=status_filter)
    return [_map_application_response(user) for user in users]


@router.put("/users/{user_id}/review", response_model=BoosterApplicationResponse)
async def review_user_application(
    user_id: int,
    payload: BoosterApplicationReviewRequest,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> BoosterApplicationResponse:
    user_service = get_user_service(db)
    updated_user = await user_service.review_booster_application(
        admin=current_admin,
        target_user_id=user_id,
        approve=payload.approve,
        booster_quota=payload.booster_quota,
        review_note=payload.review_note,
    )
    return _map_application_response(updated_user)


@router.get("/orders", response_model=OrderListResponse)
async def list_all_orders_for_admin(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    status_filter: OrderStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> OrderListResponse:
    order_service = get_order_service(db)
    orders, total = await order_service.list_orders(
        user=current_admin,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )
    pages = (total + page_size - 1) // page_size if total > 0 else 0
    return OrderListResponse(
        items=[OrderResponse.model_validate(order) for order in orders],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.put("/orders/{order_id}/intervene", response_model=OrderResponse)
async def intervene_order(
    order_id: int,
    payload: AdminOrderInterventionRequest,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> OrderResponse:
    allowed_actions = (OrderStatus.CANCELLED, OrderStatus.DISPUTED, OrderStatus.DELIVERED, OrderStatus.COMPLETED)
    if payload.action not in allowed_actions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="管理员干预仅支持取消、争议、确认交付或完结订单",
        )

    # Lock the order row so this intervention cannot race with
    # complete_order / other interventions and double-increment order_count.
    order_result = await db.execute(
        select(Order).where(Order.id == order_id).with_for_update()
    )
    order = order_result.scalar_one_or_none()
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="订单不存在",
        )

    previous_status = order.status
    order.status = payload.action
    if payload.reason:
        order.notes = f"[ADMIN] {payload.reason}" + (f"\n{order.notes}" if order.notes else "")

    if payload.action == OrderStatus.DELIVERED and previous_status != OrderStatus.DELIVERED:
        order.delivered_at = datetime.now(timezone.utc)

    if payload.action == OrderStatus.COMPLETED and previous_status != OrderStatus.COMPLETED:
        order.completed_at = datetime.now(timezone.utc)
        if order.service_id is not None:
            await db.execute(
                update(BoosterService)
                .where(BoosterService.id == order.service_id)
                .values(order_count=BoosterService.order_count + 1)
            )

    await db.flush()
    await db.refresh(order)
    await send_order_system_message(
        db=db,
        order_id=order.id,
        content=f"管理员已介入：{payload.reason}" if payload.reason else "管理员已介入处理订单",
        meta_json={
            "event": "admin_intervened",
            "order_id": order.id,
            "admin_id": current_admin.id,
            "action": payload.action.value,
            "reason": payload.reason,
        },
    )
    return OrderResponse.model_validate(order)


@router.put("/orders/{order_id}/assign", response_model=OrderResponse, summary="管理员派单")
async def assign_order(
    order_id: int,
    payload: AdminOrderAssignRequest,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> OrderResponse:
    """
    Assign a PENDING order to a booster.

    - Only PENDING orders without a booster can be assigned
    - Target must be an active BOOSTER with free quota
    - Notifies both the booster and the order owner
    """
    order_service = get_order_service(db)
    order = await order_service.assign_order(order_id, payload.booster_id, current_admin)

    await send_order_system_message(
        db=db,
        order_id=order.id,
        content=f"管理员已将订单派单给代练 {order.booster.username}"
        if order.booster is not None
        else "管理员已派单",
        meta_json={
            "event": "order_assigned",
            "order_id": order.id,
            "admin_id": current_admin.id,
            "booster_id": payload.booster_id,
            "reason": payload.reason,
        },
    )
    await notify_user(
        db,
        user_id=payload.booster_id,
        type=NotificationType.ORDER_ACCEPTED,
        title="管理员向您派单",
        content=f"管理员已将订单「{order.game_name}」派单给您，请及时处理",
        link=f"/orders/{order.id}",
        ref_id=order.id,
    )
    await notify_user(
        db,
        user_id=order.user_id,
        type=NotificationType.ORDER_ACCEPTED,
        title="订单已派单",
        content=f"您的订单「{order.game_name}」已由管理员派单给代练"
        + (f" {order.booster.username}" if order.booster is not None else ""),
        link=f"/orders/{order.id}",
        ref_id=order.id,
    )

    return OrderResponse.model_validate(order)


# =============================================================================
# Game catalog management (boss-only catalog: add / activate / reorder games)
# =============================================================================


@router.get("/games", response_model=GameListResponse, summary="游戏全量列表（含未上架）")
async def admin_list_games(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    category: GameCategory | None = Query(default=None, description="按分类筛选"),
    platform: GamePlatform | None = Query(default=None, description="按平台筛选"),
    is_active: bool | None = Query(default=None, description="按上架状态筛选"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=200),
) -> GameListResponse:
    """
    管理员游戏目录全量列表：返回全部游戏（含 is_active=0 的未上架游戏）。

    对外列表（/games，用户/打手视角）只返回 is_active=1 的游戏；
    本接口供老板后台管理使用。
    """
    filters = []
    if category is not None:
        filters.append(Game.category == category)
    if platform is not None:
        filters.append(Game.platform == platform)
    if is_active is not None:
        filters.append(Game.is_active.is_(bool(is_active)))

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
        items=[GameResponse.model_validate(g) for g in games],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.post(
    "/games",
    response_model=GameResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建游戏（默认下架）",
)
async def admin_create_game(
    payload: GameCreate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> GameResponse:
    """
    后台新建游戏。is_active 默认为 0（下架）——老板创建后可再上架。
    """
    game = Game(**payload.model_dump())
    db.add(game)
    await db.flush()
    await db.refresh(game)
    return GameResponse.model_validate(game)


@router.put("/games/{game_id}", response_model=GameResponse, summary="更新游戏（上架/下架/改名/排序）")
async def admin_update_game(
    game_id: int,
    payload: GameUpdate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> GameResponse:
    """更新游戏属性：is_active 上架/下架、name/english_name 改名、sort_order 排序等。"""
    result = await db.execute(select(Game).where(Game.id == game_id))
    game = result.scalar_one_or_none()
    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="游戏不存在",
        )
    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(game, field, value)
    await db.flush()
    await db.refresh(game)
    return GameResponse.model_validate(game)


@router.delete("/games/{game_id}", response_model=MessageResponse, summary="删除游戏")
async def admin_delete_game(
    game_id: int,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> MessageResponse:
    """
    删除游戏（硬删）。关联订单的 game_id 置空（ON DELETE SET NULL），
    关联服务卡片随删除（ON DELETE CASCADE）。
    """
    result = await db.execute(select(Game).where(Game.id == game_id))
    game = result.scalar_one_or_none()
    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="游戏不存在",
        )
    await db.delete(game)
    await db.flush()
    return MessageResponse(message="游戏已删除", success=True)


# =============================================================================
# Wallet / withdrawal management endpoints
# =============================================================================


def _map_admin_withdrawal(withdrawal) -> AdminWithdrawalResponse:
    data = WithdrawalResponse.model_validate(withdrawal).model_dump()
    username = withdrawal.user.username if withdrawal.user is not None else None
    user_email = withdrawal.user.email if withdrawal.user is not None else None
    return AdminWithdrawalResponse(**data, username=username, user_email=user_email)


@router.get("/withdrawals", response_model=AdminWithdrawalListResponse, summary="提现申请列表")
async def list_withdrawals_for_admin(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    status_filter: WithdrawalStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> AdminWithdrawalListResponse:
    """List all withdrawal requests (newest first), with applicant username."""
    wallet_service = get_wallet_service(db)
    withdrawals, total = await wallet_service.list_withdrawals(
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )
    pages = (total + page_size - 1) // page_size if total > 0 else 0
    return AdminWithdrawalListResponse(
        items=[_map_admin_withdrawal(w) for w in withdrawals],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.post("/withdrawals/{withdrawal_id}/review", response_model=AdminWithdrawalResponse, summary="审核提现申请")
async def review_withdrawal(
    withdrawal_id: int,
    payload: AdminWithdrawalReviewRequest,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> AdminWithdrawalResponse:
    """
    Approve or reject a PENDING withdrawal.

    - approve: status -> APPROVED (amount stays frozen awaiting payout)
    - reject: status -> REJECTED, frozen amount refunded to available balance
    """
    if payload.action == "reject" and not payload.reason:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="驳回提现必须填写原因",
        )

    wallet_service = get_wallet_service(db)
    withdrawal = await wallet_service.review_withdrawal(
        withdrawal_id,
        current_admin,
        approve=payload.action == "approve",
        reason=payload.reason,
    )
    return _map_admin_withdrawal(withdrawal)


@router.post("/withdrawals/{withdrawal_id}/mark-paid", response_model=AdminWithdrawalResponse, summary="标记提现已打款")
async def mark_withdrawal_paid(
    withdrawal_id: int,
    payload: AdminWithdrawalMarkPaidRequest,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> AdminWithdrawalResponse:
    """
    Mark an APPROVED withdrawal as PAID.

    Deducts the frozen amount, accumulates total_withdrawn and records
    paid_by / paid_at / payment_reference.
    """
    wallet_service = get_wallet_service(db)
    withdrawal = await wallet_service.mark_withdrawal_paid(
        withdrawal_id,
        current_admin,
        payment_reference=payload.payment_reference,
    )
    return _map_admin_withdrawal(withdrawal)


@router.post("/wallets/{user_id}/adjust", response_model=WalletResponse, summary="管理员调账")
async def adjust_wallet(
    user_id: int,
    payload: AdminWalletAdjustRequest,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> WalletResponse:
    """
    Manually adjust a user's available balance.

    Positive amount credits, negative amount debits. Zero amounts and
    adjustments that would make the balance negative are rejected.
    """
    user_result = await db.execute(select(User).where(User.id == user_id))
    if user_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )

    wallet_service = get_wallet_service(db)
    wallet = await wallet_service.get_or_create_wallet(user_id)
    await wallet_service.admin_adjust(
        wallet,
        amount=payload.amount,
        operator_id=current_admin.id,
        reason=payload.reason,
    )
    await db.refresh(wallet)
    return WalletResponse.model_validate(wallet)


# =============================================================================
# Dashboard analytics endpoints
# =============================================================================


@router.get("/dashboard/overview", response_model=OverviewStats, summary="数据看板概览")
async def dashboard_overview(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> OverviewStats:
    """平台概览统计：用户数、订单数、收入等。"""
    svc = get_dashboard_service(db)
    return await svc.get_overview()


@router.get("/dashboard/order-trend", response_model=OrderTrendResponse, summary="订单趋势")
async def dashboard_order_trend(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    period: str = Query(default="day", pattern="^(day|week|month)$"),
    days: int = Query(default=30, ge=7, le=365),
) -> OrderTrendResponse:
    """订单创建趋势图数据。"""
    svc = get_dashboard_service(db)
    return await svc.get_order_trend(period=period, days=days)


@router.get(
    "/dashboard/game-distribution",
    response_model=GameDistributionResponse,
    summary="游戏分布",
)
async def dashboard_game_distribution(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> GameDistributionResponse:
    """各游戏订单数量和收入分布。"""
    svc = get_dashboard_service(db)
    return await svc.get_game_distribution()


@router.get(
    "/dashboard/booster-ranking",
    response_model=BoosterRankingResponse,
    summary="代练排行榜",
)
async def dashboard_booster_ranking(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    limit: int = Query(default=20, ge=1, le=50),
) -> BoosterRankingResponse:
    """代练排行榜（按完成数和信誉分）。"""
    svc = get_dashboard_service(db)
    return await svc.get_booster_ranking(limit=limit)


@router.get(
    "/dashboard/user-growth",
    response_model=UserGrowthResponse,
    summary="用户增长",
)
async def dashboard_user_growth(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    days: int = Query(default=30, ge=7, le=365),
) -> UserGrowthResponse:
    """用户注册增长趋势。"""
    svc = get_dashboard_service(db)
    return await svc.get_user_growth(days=days)
