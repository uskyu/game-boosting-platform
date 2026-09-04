"""
Orders API endpoints.
Handles order creation, listing, and management operations.
"""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from app.api.chat_utils import send_order_system_message
from app.api.deps import (
    CurrentUser,
    DatabaseSession,
    get_current_booster,
)
from app.api.notification_utils import notify_boosters_new_order, notify_user
from app.core.config import settings
from app.models.notification import NotificationType
from app.models.order import ClaimLifecycleStatus, Order, OrderStatus
from app.models.user import User, UserRole
from app.schemas.order import (
    AIAnalysisResponse,
    ClaimReviewRequest,
    OrderAttachment,
    OrderDeliveryAttachment,
    OrderAnalyzeRequest,
    OrderClaimItem,
    OrderClaimListResponse,
    OrderConfirmRequest,
    OrderCreate,
    OrderDeliverRequest,
    OrderListResponse,
    OrderResponse,
    OrderUpdate,
    ClaimControlRequest,
    MyOrderClaimItem,
    MyOrderClaimListResponse,
)
from app.schemas.user import MessageResponse
from app.services.ai_service import LLMService, get_llm_service
from app.services.credit_service import get_credit_service
from app.services.file_service import save_image_bytes, validate_image_upload
from app.services.order_service import get_order_service

router = APIRouter(prefix="/orders", tags=["订单"])


def _attachment_items(order) -> list[OrderAttachment]:
    """Normalize legacy/null attachment JSON through the public schema."""
    try:
        return [OrderAttachment.model_validate(item) for item in (order.attachments or [])]
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="订单附件数据无效") from exc


def _delivery_attachment_items(attachments) -> list[OrderDeliveryAttachment]:
    """Normalize delivery proof JSON through the public schema."""
    try:
        return [OrderDeliveryAttachment.model_validate(item) for item in (attachments or [])]
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="交付附件数据无效") from exc


def _serialize_order(order, viewer: User) -> OrderResponse:
    """Serialize an order, redacting game_account from viewers who don't own
    it, weren't assigned to it, and aren't admins.
    Otherwise a booster browsing the PENDING list could harvest every
    user's game account.

    boss_contact（老板联系 ID）同样仅对 发布人/管理员/首抢打手 直接可见；
    其他已接单打手的可见性由 _enrich_* 依据 my_claim 回填。
    """
    response = OrderResponse.model_validate(order)
    if viewer.role == UserRole.ADMIN:
        return response
    if viewer.id == order.user_id:
        return response
    if order.booster_id is not None and viewer.id == order.booster_id:
        return response
    return response.model_copy(update={"game_account": None, "boss_contact": None})


async def _enrich_order_response(db, response: OrderResponse, order, viewer: User) -> OrderResponse:
    """Attach viewer-dependent claim info to a single order response.

    Admins and order publishers get aggregate claim status counts. Non-admins
    also get their own claim (when present) and the boss contact.
    """
    order_service = get_order_service(db)
    if viewer.role == UserRole.ADMIN or order.user_id == viewer.id:
        counts = await order_service.claim_status_counts([order.id])
        status_counts = counts.get(order.id, {})
        response.pending_review_count = status_counts.get(
            ClaimLifecycleStatus.DELIVERED.value, 0
        )
        response.settled_count = status_counts.get(ClaimLifecycleStatus.SETTLED.value, 0)

    if viewer.role != UserRole.ADMIN:
        claim_view = await order_service.get_order_claim_view(order, viewer)
        if claim_view is not None:
            response.my_claim = OrderClaimItem.model_validate(claim_view)
            # 已接单打手可见老板联系方式
            if response.boss_contact is None:
                response.boss_contact = order.boss_contact
    return response


async def _enrich_order_responses(
    db, responses: list[OrderResponse], orders: list[Order], viewer: User
) -> list[OrderResponse]:
    """Batched version of _enrich_order_response for list endpoints."""
    if not orders:
        return responses
    order_service = get_order_service(db)
    orders_by_id = {order.id: order for order in orders}

    # One grouped query covers every order the viewer may manage. For admins
    # that is the complete page; for regular users it is only their own orders.
    count_order_ids = [
        order.id
        for order in orders
        if viewer.role == UserRole.ADMIN or order.user_id == viewer.id
    ]
    if count_order_ids:
        counts = await order_service.claim_status_counts(count_order_ids)
        for response in responses:
            status_counts = counts.get(response.id, {})
            response.pending_review_count = status_counts.get(
                ClaimLifecycleStatus.DELIVERED.value, 0
            )
            response.settled_count = status_counts.get(
                ClaimLifecycleStatus.SETTLED.value, 0
            )

    if viewer.role != UserRole.ADMIN:
        claim_views = await order_service.claims_view_for_booster(orders, viewer)
        for response in responses:
            claim_view = claim_views.get(response.id)
            if claim_view is not None:
                response.my_claim = OrderClaimItem.model_validate(claim_view)
                # 已接单打手可见老板联系方式；_serialize_order 已覆盖首抢，
                # 这里补齐其他名额接单者的可见性。
                if response.boss_contact is None:
                    response.boss_contact = orders_by_id[response.id].boss_contact
    return responses


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
    summary="发布订单（所有注册用户）",
    description="所有注册用户可发布；非管理员发布时托管 发单价格×可接单人数",
)
async def create_order(
    order_data: OrderCreate,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> OrderResponse:
    """
    Create a new boosting order.

    Requires authentication. All registered users may publish orders;
    non-admin publishers escrow price x max_claims at creation.

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

    # 任何人发单：向所有活跃非管理员广播"新订单"通知（批量、单事务，排除自己）
    await notify_boosters_new_order(db, order=order, exclude_user_id=current_user.id)

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
    mine_published: Annotated[
        bool,
        Query(description="仅查看当前用户发布的订单"),
    ] = False,
    boss_contact: Annotated[
        str | None,
        Query(description="按老板ID模糊筛选", max_length=64),
    ] = None,
) -> OrderListResponse:
    """
    List orders with filtering and pagination.

    - Non-admin users see claimable pending orders and their own/assigned orders
    - Admins see all orders

    Filters:
    - **game_name**: Filter by game name (partial match)
    - **status**: Filter by order status
    - **mine_published**: Only orders published by the current user
    - **boss_contact**: Filter by boss contact ID (partial match)

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
        mine_published=mine_published,
        boss_contact=boss_contact,
    )

    # Calculate total pages
    pages = (total + page_size - 1) // page_size if total > 0 else 0

    responses = [_serialize_order(order, current_user) for order in orders]
    responses = await _enrich_order_responses(db, responses, orders, current_user)

    return OrderListResponse(
        items=responses,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.post(
    "/{order_id}/attachments",
    response_model=OrderAttachment,
    status_code=status.HTTP_201_CREATED,
    summary="上传订单图片附件",
)
async def upload_order_attachment(
    order_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
    attachment: UploadFile = File(...),
) -> OrderAttachment:
    """Upload one validated image after the order has been created."""
    order = await get_order_service(db).get_order_by_id(order_id, current_user)
    if current_user.role not in (UserRole.ADMIN, UserRole.USER) or (
        current_user.role != UserRole.ADMIN and order.user_id != current_user.id
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权修改此订单附件")
    attachments = _attachment_items(order)
    if len(attachments) >= 5:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="订单最多上传5张图片")
    data, suffix = await validate_image_upload(attachment, max_size_bytes=10 * 1024 * 1024)
    attachment_name = Path(attachment.filename or "attachment").name
    if not attachment_name or len(attachment_name) > 255:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="附件文件名无效")
    content_type = (attachment.content_type or "").lower()
    url = save_image_bytes(data, suffix, "orders")
    item = OrderAttachment(
        url=url,
        name=attachment_name,
        size=len(data),
        content_type=content_type,
    )
    attachments.append(item)
    order.attachments = [entry.model_dump() for entry in attachments]
    await db.flush()
    return item


@router.delete(
    "/{order_id}/attachments/{attachment_index}",
    response_model=OrderResponse,
    summary="删除订单图片附件",
)
async def delete_order_attachment(
    order_id: int,
    attachment_index: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> OrderResponse:
    order = await get_order_service(db).get_order_by_id(order_id, current_user)
    if current_user.role not in (UserRole.ADMIN, UserRole.USER) or (
        current_user.role != UserRole.ADMIN and order.user_id != current_user.id
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权修改此订单附件")
    attachments = _attachment_items(order)
    if attachment_index < 0 or attachment_index >= len(attachments):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="附件索引越界")
    item = attachments.pop(attachment_index)
    relative = item.url.removeprefix("/uploads/")
    upload_root = Path(settings.UPLOAD_DIR).resolve()
    file_path = (upload_root / relative).resolve()
    if upload_root.resolve() not in file_path.parents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="附件路径无效")
    file_path.unlink(missing_ok=True)
    order.attachments = [entry.model_dump() for entry in attachments]
    await db.flush()
    await db.refresh(order)
    return OrderResponse.model_validate(order)


@router.post(
    "/{order_id}/deliver-attachments",
    response_model=OrderDeliveryAttachment,
    status_code=status.HTTP_201_CREATED,
    summary="上传交付附件",
)
async def upload_deliver_attachment(
    order_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
    attachment: UploadFile = File(..., description="交付图片附件"),
) -> OrderDeliveryAttachment:
    """Upload a validated delivery proof image for the caller's own claim.

    权限：仅已报名（存在 claim 且未结算）的打手；ADMIN 不参与交付走原有后台。
    校验：单张 <=10MB png/jpeg/webp，校验 Magic/MIME/扩展。
    存储：uploads/deliveries 随机文件名，返回 {url,name,size,content_type} 并
    append 到调用者自己 claim 的 delivery_attachments。
    限制：每个名额最多5张。
    """
    if current_user.role == UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="管理员不参与交付附件")
    order_service = get_order_service(db)
    order, claim = await order_service.get_my_claim_for_delivery(order_id, current_user)

    items = _delivery_attachment_items(claim.delivery_attachments)
    if len(items) >= 5:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="每单最多上传5张交付附件")
    data_bytes, suffix = await validate_image_upload(attachment, max_size_bytes=10 * 1024 * 1024)
    attachment_name = Path(attachment.filename or "attachment").name
    if not attachment_name or len(attachment_name) > 255:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="附件文件名无效")
    content_type = (attachment.content_type or "").lower()
    url = save_image_bytes(data_bytes, suffix, "deliveries")
    item = OrderDeliveryAttachment(
        url=url,
        name=attachment_name,
        size=len(data_bytes),
        content_type=content_type,
    )
    items.append(item)
    claim.delivery_attachments = [entry.model_dump() for entry in items]
    await db.flush()
    return item


@router.delete(
    "/{order_id}/deliver-attachments/{attachment_index}",
    response_model=OrderResponse,
    summary="删除交付附件",
)
async def delete_deliver_attachment(
    order_id: int,
    attachment_index: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> OrderResponse:
    """Delete a delivery proof image by index from the caller's own claim."""
    if current_user.role == UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="管理员不参与交付附件")
    order_service = get_order_service(db)
    order, claim = await order_service.get_my_claim_for_delivery(order_id, current_user)

    items = _delivery_attachment_items(claim.delivery_attachments)
    if attachment_index < 0 or attachment_index >= len(items):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="附件索引越界")
    item = items.pop(attachment_index)
    relative = item.url.removeprefix("/uploads/")
    upload_root = Path(settings.UPLOAD_DIR).resolve()
    file_path = (upload_root / relative).resolve()
    if upload_root.resolve() not in file_path.parents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="附件路径无效")
    file_path.unlink(missing_ok=True)
    claim.delivery_attachments = [entry.model_dump() for entry in items]
    await db.flush()
    await db.refresh(order)
    return OrderResponse.model_validate(order)


@router.get(
    "/claims/mine",
    response_model=MyOrderClaimListResponse,
    summary="我的报名记录",
    description="分页获取当前用户的订单报名记录（名额），按记录ID倒序，支持按状态筛选",
)
async def list_my_claims(
    current_user: CurrentUser,
    db: DatabaseSession,
    status_filter: Annotated[
        ClaimLifecycleStatus | None,
        Query(alias="status", description="按名额状态筛选：CLAIMED/DELIVERED/SETTLED"),
    ] = None,
    page: Annotated[
        int,
        Query(ge=1, description="页码"),
    ] = 1,
    page_size: Annotated[
        int,
        Query(ge=1, le=100, description="每页数量"),
    ] = 20,
) -> MyOrderClaimListResponse:
    """
    List the current user's claims (我的报名) with the parent order summary.

    - Non-admin accounts only (admins do not claim orders)
    - Ordered by claim id descending (newest first)
    - Each item carries the claim lifecycle fields plus an ``order`` summary
    """
    if current_user.role == UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="管理员不参与报名，无法查看报名记录",
        )

    order_service = get_order_service(db)
    items, total = await order_service.list_my_claims(
        current_user.id,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )
    pages = (total + page_size - 1) // page_size if total > 0 else 0
    return MyOrderClaimListResponse(
        items=[MyOrderClaimItem.model_validate(item) for item in items],
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
    - Boosters can view pending/claimable orders or orders they claimed
    - Admins can view all orders
    """
    order_service = get_order_service(db)

    order = await order_service.get_order_by_id(order_id, current_user)

    response = _serialize_order(order, current_user)
    return await _enrich_order_response(db, response, order, current_user)


@router.get(
    "/{order_id}/claims",
    response_model=OrderClaimListResponse,
    summary="获取订单报名名单",
    description="管理员查看订单的全部打手报名记录（按报名时间升序，标注首抢与名额状态）",
)
async def list_order_claims(
    order_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> OrderClaimListResponse:
    """
    List booster claims (报名名单) for an order. ADMIN or order publisher.

    - Ordered by claim time ascending (earliest grab first)
    - is_first marks the claim whose booster is the order's current booster
    - Each claim carries its CLAIMED/DELIVERED/SETTLED lifecycle fields
    """
    result = await db.execute(select(Order.id, Order.user_id).where(Order.id == order_id))
    order_row = result.one_or_none()
    if order_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="订单不存在",
        )
    # 人人可发单模式：发单用户自己管理/审核报名名单，管理员兜底
    if current_user.role != UserRole.ADMIN and order_row.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有订单发布人或管理员才能查看报名名单",
        )

    order_service = get_order_service(db)

    claims = await order_service.list_order_claims(order_id)

    return OrderClaimListResponse(
        items=[OrderClaimItem.model_validate(claim) for claim in claims],
        total=len(claims),
    )


@router.put(
    "/{order_id}/claims/{claim_id}/review",
    response_model=OrderClaimItem,
    summary="审核交付记录",
    description="管理员审核某个名额的交付记录（action=approve 通过并结算该打手）",
)
async def review_order_claim(
    order_id: int,
    claim_id: int,
    payload: ClaimReviewRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> OrderClaimItem:
    """
    Review one booster's delivered claim (名额审核).

    - Order publisher or ADMIN can review; action must be 'approve'
    - The claim must belong to the order and be DELIVERED
    - amount（可选）：部分到账金额，缺省按订单全额结算（上限 max(price, price_max)）
    - note（可选）：打款备注，随钱包流水留存
    - The order auto-completes when every claim is settled and the quota is
      exhausted (or claiming closed)
    """
    order_service = get_order_service(db)
    claim = await order_service.review_claim(
        order_id,
        claim_id,
        current_user,
        action=payload.action,
        payout_amount=payload.amount,
        note=payload.note,
        deduction=payload.deduction,
    )
    return OrderClaimItem.model_validate(claim)


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
    description="非管理员用户接受订单（管理员请通过 /admin/orders/{id}/intervene 干预）",
)
async def accept_order(
    order_id: int,
    current_user: Annotated[User, Depends(get_current_booster)],
    db: DatabaseSession,
) -> OrderResponse:
    """
    Accept an order as a booster.

    - Any non-admin authenticated user can accept orders
    - Only PENDING orders can be accepted
    - Cannot accept your own order
    - Admin state changes go through /admin/orders/{id}/intervene, not here
    """
    order_service = get_order_service(db)

    order = await order_service.accept_order(order_id, current_user)
    await send_order_system_message(
        db=db,
        order_id=order.id,
        content=f"打手 {current_user.username} 已接手订单",
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
        title="订单已被接手",
        content=f"打手 {current_user.username} 已接手您的订单「{order.game_name}」",
        link=f"/orders/{order.id}",
        ref_id=order.id,
    )

    # 消息/通知写入与订单不在同一加载上下文，序列化前刷新订单，
    # 防止 expired 属性在同步属性访问时触发 MissingGreenlet 500。
    await db.refresh(order)

    return OrderResponse.model_validate(order)


@router.put(
    "/{order_id}/deliver",
    response_model=OrderResponse,
    summary="结束订单",
    description="打手提交自己名额的交付汇报，等待老板/管理员逐个审核；订单保持可被其他打手报名",
)
async def deliver_order(
    order_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
    payload: OrderDeliverRequest | None = None,
) -> OrderResponse:
    """
    Booster ends their claim on the order.

    - Only boosters with a CLAIMED claim on the order can deliver
    - Only PENDING/LOCKED orders can be delivered against
    - The claim becomes DELIVERED; the order status is not changed
    - Admin-driven state changes go through /admin/orders/{id}/intervene
    """
    order_service = get_order_service(db)

    delivery_note = None
    if payload is not None:
        delivery_note = payload.delivery_note if payload.delivery_note is not None else payload.notes

    order, claim = await order_service.deliver_order(order_id, current_user, delivery_note=delivery_note)
    await send_order_system_message(
        db=db,
        order_id=order.id,
        content=f"打手 {current_user.username} 已结束订单，等待确认",
        meta_json={
            "event": "order_delivered",
            "order_id": order.id,
            "booster_id": current_user.id,
            "claim_id": claim.id,
        },
    )
    await notify_user(
        db,
        user_id=order.user_id,
        type=NotificationType.ORDER_DELIVERED,
        title="打手已结束订单",
        content=f"订单「{order.game_name}」打手已结束订单，请确认",
        link=f"/orders/{order.id}",
        ref_id=order.id,
    )

    # 消息/通知写入与订单不在同一加载上下文，序列化前刷新订单，
    # 防止 expired 属性在同步属性访问时触发 MissingGreenlet 500。
    await db.refresh(order)
    await db.refresh(claim)

    response = _serialize_order(order, current_user)
    claim_view = await order_service.get_order_claim_view(order, current_user)
    if claim_view is not None:
        response.my_claim = OrderClaimItem.model_validate(claim_view)
    return response


@router.put(
    "/{order_id}/confirm",
    response_model=OrderResponse,
    summary="确认完成",
    description="老板确认订单完成：结算全部待审核名额；可携带 amount 部分到账与 note 打款备注（仅单个待审核时）",
)
async def confirm_order(
    order_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
    payload: OrderConfirmRequest | None = None,
) -> OrderResponse:
    """
    Boss confirms order completion (compat endpoint).

    - Only order owner or admin can confirm
    - Settles every DELIVERED claim of the order at full price
    - payload.amount（可选）：仅当恰好一个待审核名额时可传，表示部分到账
    - payload.note（可选）：打款备注，随钱包流水留存
    - Order auto-completes when all claims are settled and the quota is full
    """
    order_service = get_order_service(db)

    payout_amount = payload.amount if payload else None
    note = (payload.note or None) if payload else None
    order = await order_service.confirm_order(
        order_id, current_user, payout_amount=payout_amount, note=note
    )
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
    # 争议发起同时通知管理员（管理派单台路径），发起人是管理员时不再重复通知
    if current_user.role != UserRole.ADMIN:
        admin_result = await db.execute(
            select(User.id).where(User.role == UserRole.ADMIN, User.is_active.is_(True))
        )
        for (admin_id,) in admin_result.all():
            if admin_id == current_user.id:
                continue
            await notify_user(
                db,
                user_id=admin_id,
                type=NotificationType.ORDER_DISPUTED,
                title="订单争议待处理",
                content=f"订单 #{order.id}「{order.game_name}」被 {current_user.username} 发起争议",
                link=f"/admin/dispatch/{order.id}",
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


@router.put("/{order_id}/claim-control", response_model=OrderResponse, summary="订单抢单控制")
async def claim_control(order_id: int, payload: ClaimControlRequest, current_user: CurrentUser, db: DatabaseSession) -> OrderResponse:
    order = await get_order_service(db).claim_control(order_id, payload.action, current_user)
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

    await order_service.delete_order(order_id, current_user)
    return MessageResponse(message="订单已删除", success=True)
