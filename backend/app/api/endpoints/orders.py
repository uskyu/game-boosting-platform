"""
Orders API endpoints.
Handles order creation, listing, and management operations.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.chat_utils import send_order_system_message
from app.api.deps import (
    CurrentUser,
    DatabaseSession,
    get_current_booster,
)
from app.api.notification_utils import notify_user
from app.models.notification import NotificationType
from app.models.order import OrderStatus
from app.models.user import User, UserRole
from app.schemas.order import (
    AIAnalysisResponse,
    OrderAnalyzeRequest,
    OrderCreate,
    OrderListResponse,
    OrderResponse,
    OrderUpdate,
)
from app.schemas.user import MessageResponse
from app.services.ai_service import LLMService, get_llm_service
from app.services.credit_service import get_credit_service
from app.services.order_service import get_order_service

router = APIRouter(prefix="/orders", tags=["订单"])


def _serialize_order(order, viewer: User) -> OrderResponse:
    """Serialize an order, redacting game_account from viewers who don't own
    it, weren't assigned to it, and aren't admins.
    Otherwise a booster browsing the PENDING list could harvest every
    user's game account."""
    response = OrderResponse.model_validate(order)
    if viewer.role == UserRole.ADMIN:
        return response
    if viewer.id == order.user_id:
        return response
    if order.booster_id is not None and viewer.id == order.booster_id:
        return response
    return response.model_copy(update={"game_account": None})


@router.post(
    "/analyze",
    response_model=AIAnalysisResponse,
    summary="AI分析需求",
    description="使用AI分析用户的游戏代练需求描述，提取结构化信息",
)
async def analyze_requirement(
    request: OrderAnalyzeRequest,
    db: DatabaseSession,
    current_user: CurrentUser,
    llm_service: Annotated[LLMService, Depends(get_llm_service)],
) -> AIAnalysisResponse:
    """
    Analyze user requirement using AI.

    - **description**: Natural language description of boosting requirements

    Returns structured data extracted from the description including:
    - game_name: Name of the game
    - current_rank: Current player rank
    - target_rank: Desired rank
    - price: Budget amount
    - role: Game role/position
    - server: Game server/region
    - is_risky: Flag for prohibited content
    """
    order_service = get_order_service(db)
    result = await order_service.analyze_requirement(request.description, llm_service)

    # Check for risky content
    if result.get("is_risky", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="需求描述包含违规内容，请修改后重试",
        )

    return AIAnalysisResponse(
        game_id=result.get("game_id"),
        game_name=result.get("game_name"),
        current_rank=result.get("current_rank"),
        target_rank=result.get("target_rank"),
        price=result.get("price"),
        role=result.get("role"),
        server=result.get("server"),
        service_type=result.get("service_type"),
        ai_tags=result.get("ai_tags"),
        is_risky=result.get("is_risky", False),
    )


@router.post(
    "/create",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建订单",
    description="根据结构化数据创建新的代练订单",
)
async def create_order(
    order_data: OrderCreate,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> OrderResponse:
    """
    Create a new boosting order.

    Requires authentication. Users cannot create orders with BOOSTER role.

    - **game_name**: Name of the game
    - **current_rank**: Current player rank
    - **target_rank**: Desired rank
    - **price**: Order price
    - **description_raw**: Original description (optional)
    - **game_account**: Game account credentials (optional)
    - **game_password**: Game password (optional)
    """
    order_service = get_order_service(db)

    order = await order_service.create_order(order_data, current_user)

    return OrderResponse.model_validate(order)


@router.get(
    "/",
    response_model=OrderListResponse,
    summary="获取订单列表",
    description="获取订单列表，支持分页和筛选",
)
async def list_orders(
    current_user: CurrentUser,
    db: DatabaseSession,
    game_name: Annotated[
        str | None,
        Query(description="按游戏名称筛选", max_length=100),
    ] = None,
    status_filter: Annotated[
        OrderStatus | None,
        Query(alias="status", description="按订单状态筛选"),
    ] = None,
    page: Annotated[
        int,
        Query(ge=1, description="页码"),
    ] = 1,
    page_size: Annotated[
        int,
        Query(ge=1, le=100, description="每页数量"),
    ] = 20,
) -> OrderListResponse:
    """
    List orders with filtering and pagination.

    - Users see only their own orders
    - Boosters see pending orders and their assigned orders
    - Admins see all orders

    Filters:
    - **game_name**: Filter by game name (partial match)
    - **status**: Filter by order status

    Pagination:
    - **page**: Page number (default: 1)
    - **page_size**: Items per page (default: 20, max: 100)
    """
    order_service = get_order_service(db)

    orders, total = await order_service.list_orders(
        user=current_user,
        game_name=game_name,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )

    # Calculate total pages
    pages = (total + page_size - 1) // page_size if total > 0 else 0

    return OrderListResponse(
        items=[_serialize_order(order, current_user) for order in orders],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get(
    "/{order_id}",
    response_model=OrderResponse,
    summary="获取订单详情",
    description="根据订单ID获取订单详细信息",
)
async def get_order(
    order_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> OrderResponse:
    """
    Get order details by ID.

    Access control:
    - Users can only view their own orders
    - Boosters can view pending orders or their assigned orders
    - Admins can view all orders
    """
    order_service = get_order_service(db)

    order = await order_service.get_order_by_id(order_id, current_user)

    return _serialize_order(order, current_user)


@router.put(
    "/{order_id}",
    response_model=OrderResponse,
    summary="更新订单",
    description="更新订单信息（仅限待接单状态）",
)
async def update_order(
    order_id: int,
    order_data: OrderUpdate,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> OrderResponse:
    """
    Update order details.

    Only pending orders can be updated.
    Only order owner or admin can update.
    """
    order_service = get_order_service(db)

    order = await order_service.update_order(order_id, order_data, current_user)

    return OrderResponse.model_validate(order)


@router.put(
    "/{order_id}/accept",
    response_model=OrderResponse,
    summary="接受订单",
    description="代练接受订单（仅限代练角色，管理员请通过 /admin/orders/{id}/intervene 干预）",
)
async def accept_order(
    order_id: int,
    current_user: Annotated[User, Depends(get_current_booster)],
    db: DatabaseSession,
) -> OrderResponse:
    """
    Accept an order as a booster.

    - Only users with the BOOSTER role can accept orders
    - Only PENDING orders can be accepted
    - Cannot accept your own order
    - Admin state changes go through /admin/orders/{id}/intervene, not here
    """
    if current_user.role != UserRole.BOOSTER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="权限不足，只有代练才能接单",
        )

    order_service = get_order_service(db)

    order = await order_service.accept_order(order_id, current_user)
    await send_order_system_message(
        db=db,
        order_id=order.id,
        content=f"代练 {current_user.username} 已接单",
        meta_json={
            "event": "order_accepted",
            "order_id": order.id,
            "booster_id": current_user.id,
        },
    )
    await notify_user(
        db,
        user_id=order.user_id,
        type=NotificationType.ORDER_ACCEPTED,
        title="订单已被接单",
        content=f"代练 {current_user.username} 已接受您的订单「{order.game_name}」",
        link=f"/orders/{order.id}",
        ref_id=order.id,
    )

    return OrderResponse.model_validate(order)


@router.put(
    "/{order_id}/deliver",
    response_model=OrderResponse,
    summary="提交完成",
    description="代练提交订单完成，等待客户确认",
)
async def deliver_order(
    order_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> OrderResponse:
    """
    Booster submits order as delivered.

    - Only the assigned booster can deliver
    - Only LOCKED orders can be delivered
    - Customer must confirm to finalize
    - Admin-driven state changes go through /admin/orders/{id}/intervene
    """
    order_service = get_order_service(db)

    order = await order_service.deliver_order(order_id, current_user)
    await send_order_system_message(
        db=db,
        order_id=order.id,
        content=f"代练 {current_user.username} 已提交完成，等待确认",
        meta_json={
            "event": "order_delivered",
            "order_id": order.id,
            "booster_id": current_user.id,
        },
    )
    await notify_user(
        db,
        user_id=order.user_id,
        type=NotificationType.ORDER_DELIVERED,
        title="代练已提交完成",
        content=f"订单「{order.game_name}」代练已提交完成，请确认",
        link=f"/orders/{order.id}",
        ref_id=order.id,
    )

    return OrderResponse.model_validate(order)


@router.put(
    "/{order_id}/confirm",
    response_model=OrderResponse,
    summary="确认完成",
    description="客户确认订单完成",
)
async def confirm_order(
    order_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> OrderResponse:
    """
    Customer confirms order completion.

    - Only order owner or admin can confirm
    - Only DELIVERED orders can be confirmed
    """
    order_service = get_order_service(db)

    order = await order_service.confirm_order(order_id, current_user)
    await send_order_system_message(
        db=db,
        order_id=order.id,
        content="客户已确认完成，订单结束",
        meta_json={
            "event": "order_completed",
            "order_id": order.id,
        },
    )

    # Recalculate booster credit after order completion
    if order.booster_id:
        credit_service = get_credit_service(db)
        await credit_service.recalculate(order.booster_id)
        await notify_user(
            db,
            user_id=order.booster_id,
            type=NotificationType.ORDER_CONFIRMED,
            title="订单已确认完成",
            content=f"客户已确认订单「{order.game_name}」完成",
            link=f"/orders/{order.id}",
            ref_id=order.id,
        )

    return OrderResponse.model_validate(order)


@router.put(
    "/{order_id}/cancel",
    response_model=OrderResponse,
    summary="取消订单",
    description="取消订单（仅限待接单状态）",
)
async def cancel_order(
    order_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> OrderResponse:
    """
    Cancel an order.

    - Only order owner or admin can cancel
    - Only PENDING orders can be cancelled by users
    - Admin can cancel PENDING or LOCKED orders
    """
    order_service = get_order_service(db)

    order = await order_service.cancel_order(order_id, current_user)
    await send_order_system_message(
        db=db,
        order_id=order.id,
        content="订单已取消",
        meta_json={
            "event": "order_cancelled",
            "order_id": order.id,
            "operator_id": current_user.id,
        },
    )
    # Notify the other party
    target_id = order.booster_id if current_user.id == order.user_id else order.user_id
    if target_id:
        await notify_user(
            db,
            user_id=target_id,
            type=NotificationType.ORDER_CANCELLED,
            title="订单已取消",
            content=f"订单「{order.game_name}」已被取消",
            link=f"/orders/{order.id}",
            ref_id=order.id,
        )

    return OrderResponse.model_validate(order)


@router.put(
    "/{order_id}/dispute",
    response_model=OrderResponse,
    summary="发起争议",
    description="对订单发起争议",
)
async def dispute_order(
    order_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
    reason: Annotated[
        str | None,
        Query(description="争议原因", max_length=500),
    ] = None,
) -> OrderResponse:
    """
    Raise a dispute on an order.

    - Only order owner, assigned booster, or admin can dispute
    - Only LOCKED, DELIVERED, or COMPLETED orders can be disputed
    """
    order_service = get_order_service(db)

    order = await order_service.dispute_order(order_id, current_user, reason)
    await send_order_system_message(
        db=db,
        order_id=order.id,
        content=f"{current_user.username} 发起了纠纷",
        meta_json={
            "event": "order_disputed",
            "order_id": order.id,
            "operator_id": current_user.id,
            "reason": reason,
        },
    )
    target_id = order.booster_id if current_user.id == order.user_id else order.user_id
    if target_id:
        await notify_user(
            db,
            user_id=target_id,
            type=NotificationType.ORDER_DISPUTED,
            title="订单争议",
            content=f"订单「{order.game_name}」被发起争议",
            link=f"/orders/{order.id}",
            ref_id=order.id,
        )

    return OrderResponse.model_validate(order)


@router.put(
    "/{order_id}/pay",
    response_model=OrderResponse,
    summary="确认支付",
    description="模拟支付订单",
)
async def pay_order(
    order_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> OrderResponse:
    """Simulate payment for an order. Only order owner can pay."""
    order_service = get_order_service(db)
    order = await order_service.pay_order(order_id, current_user)
    return OrderResponse.model_validate(order)


@router.put(
    "/{order_id}/refund",
    response_model=OrderResponse,
    summary="退款",
    description="管理员退款（订单须为已取消或争议状态）",
)
async def refund_order(
    order_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> OrderResponse:
    """Refund a paid order. Admin only, order must be CANCELLED or DISPUTED."""
    order_service = get_order_service(db)
    order = await order_service.refund_order(order_id, current_user)
    return OrderResponse.model_validate(order)


@router.delete(
    "/{order_id}",
    response_model=MessageResponse,
    summary="删除订单",
    description="删除订单（仅限管理员）",
)
async def delete_order(
    order_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> MessageResponse:
    """
    Delete an order (admin only).

    This is a soft operation - prefer using cancel/dispute for normal workflows.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="权限不足，只有管理员才能删除订单",
        )

    order_service = get_order_service(db)

    # Verify order exists
    order = await order_service.get_order_by_id(order_id)

    # Delete order
    await db.delete(order)
    await db.flush()

    return MessageResponse(message="订单已删除", success=True)
