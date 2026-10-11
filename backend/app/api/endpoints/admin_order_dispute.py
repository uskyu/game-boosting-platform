"""Administrator endpoint for cancelling (revoking) an order dispute."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DatabaseSession, get_current_admin
from app.api.notification_utils import notify_user
from app.api.order_events import broadcast_order_state_changed
from app.models.notification import NotificationType
from app.models.user import User
from app.schemas.order import OrderResponse
from app.services.order_service import get_order_service

admin_order_dispute_router = APIRouter(prefix="/admin", tags=["admin"])


@admin_order_dispute_router.put(
    "/orders/{order_id}/dispute/cancel",
    response_model=OrderResponse,
    summary="取消争议（撤销平台介入）",
    description="撤销订单争议，订单回到争议前状态（校验与恢复逻辑在 service 层）",
)
async def cancel_order_dispute(
    order_id: int,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> OrderResponse:
    """
    Cancel an ongoing dispute, rolling the order back to its pre-dispute state.

    - Only admins can call this endpoint.
    - Validation/rollback (not disputed, refunded, settled, funds posted, ...)
      lives in OrderService.cancel_dispute and raises 400 with a Chinese detail.
    - Notifies both the order owner and the booster, then broadcasts the
      order state change so open order-hall pages refresh.
    """
    order_service = get_order_service(db)
    order = await order_service.cancel_dispute(order_id, current_admin)

    for user_id in (order.user_id, order.booster_id):
        if user_id is None or user_id == current_admin.id:
            continue
        await notify_user(
            db,
            user_id=user_id,
            type=NotificationType.ORDER_DISPUTED,
            title="争议已撤销",
            content=f"订单「{order.game_name}」的争议已被管理员撤销，订单已回到争议前的状态，请留意后续处理",
            link=f"/orders/{order.id}",
            ref_id=order.id,
        )

    await broadcast_order_state_changed(
        order_id=order.id,
        status=order.status,
        claim_status=order.claim_status,
        claimed_count=order.claimed_count,
    )

    return OrderResponse.model_validate(order)
