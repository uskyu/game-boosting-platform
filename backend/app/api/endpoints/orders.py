"""
Orders API endpoints.
Handles order creation, listing, and management operations.
"""

import asyncio
import base64
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from app.api.chat_utils import send_order_system_message
from app.api.order_events import broadcast_order_state_changed
from app.api.deps import (
    CurrentUser,
    DatabaseSession,
    get_current_booster,
)
from app.api.notification_utils import notify_boosters_new_order, notify_user
from app.core.client_ip import client_ip_from_request
from app.core.config import settings
from app.core.post_commit import run_after_commit
from app.core.request_timing import (
    STAGE_ACCEPT_SERVICE,
    STAGE_ACCEPT_SIDE_EFFECTS,
    STAGE_ORDER_NOTIFICATIONS,
    STAGE_ORDER_SERVICE,
    measure_stage,
)
from app.models.notification import NotificationType
from app.models.order import ClaimLifecycleStatus, ClaimStatus, Order, OrderClaim, OrderStatus
from app.models.order_cancel_request import OrderCancelRequestStatus
from app.models.user import User, UserRole
from app.schemas.order import (
    AIAnalysisResponse,
    CreateOrderCancelRequest,
    DecideOrderCancelRequest,
    OrderCancelRequestItem,
    OrderCancelRequestListResponse,
    ClaimReviewRequest,
    OrderAcceptRequest,
    OrderAttachment,
    OrderDeliveryAttachment,
    OrderDeliveryAttachmentsResponse,
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
    HallDigestResponse,
    MyOrderClaimItem,
    MyOrderClaimListResponse,
)
from app.schemas.user import MessageResponse
from app.services import captcha_service
from app.services.ai_service import LLMService, get_llm_service
from app.services.credit_service import get_credit_service
from app.services.file_service import save_image_bytes, validate_image_upload
from app.services.order_service import get_order_service
from app.services.site_setting_service import get_or_create_site_setting

router = APIRouter(prefix="/orders", tags=["订单"])
logger = logging.getLogger(__name__)


class AcceptCaptchaResponse(BaseModel):
    """接单验证码响应：结构对齐 /auth/captcha，前端可复用同一弹窗组件。"""

    captcha_id: str = Field(description="验证码ID")
    image: str = Field(description="base64 data URI 图片")


def _accept_captcha_invalid() -> HTTPException:
    """接单验证码校验失败：结构化 detail，前端按 code 定位输入框。"""
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": "captcha_invalid", "message": "验证码错误或已过期"},
    )


async def _cleanup_rejected_delivery_files(urls: list[str]) -> None:
    """Remove rejected proof images only after the transaction commits."""
    upload_root = Path(settings.UPLOAD_DIR).resolve()
    for url in urls:
        if not isinstance(url, str) or not url.startswith("/uploads/deliveries/"):
            continue
        file_path = (upload_root / url.removeprefix("/uploads/")).resolve()
        if upload_root not in file_path.parents:
            continue
        try:
            await asyncio.to_thread(file_path.unlink, missing_ok=True)
        except OSError:
            logger.warning("Failed to remove rejected delivery image %s", file_path)


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


async def _apply_accept_window(
    db, responses: list[OrderResponse], orders: list[Order], viewer: User
) -> None:
    """按查看者的保证金档位，为订单补上「还需等待多少秒才能接单」。

    保证金模式关闭或查看者是管理员时不写任何字段，前端按「可立即接单」渲染。
    """
    if viewer.role == UserRole.ADMIN:
        return
    # 保证金开关/档位等待秒数：列表链路已由 list_orders 的聚合顺带取回（请求内
    # 缓存复用），详情等其它路径回退 deposit_service 原实现，二者同口径。
    order_service = get_order_service(db)
    deposit_enabled, wait_seconds = await order_service.accept_window_wait(viewer)
    if not deposit_enabled:
        return
    now = datetime.now(timezone.utc)
    orders_by_id = {order.id: order for order in orders}
    for response in responses:
        order = orders_by_id.get(response.id)
        if order is None:
            continue

        # The wait window only describes an order that this viewer can still
        # accept. Once the order is full/closed, the viewer already has a
        # claim, or the deadline has passed, exposing the original publish
        # timestamp makes the UI show a stale countdown beside a grabbed
        # order.
        deadline = order.deadline
        if deadline is not None:
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            if deadline <= now:
                continue
        if (
            order.user_id == viewer.id
            or order.is_archived
            or order.status not in (OrderStatus.PENDING, OrderStatus.LOCKED)
            or order.claim_status != ClaimStatus.OPEN
            or order.claimed_count >= order.max_claims
            or (order.status == OrderStatus.LOCKED and order.max_claims <= 1)
            or response.my_claim is not None
        ):
            continue

        response.accept_wait_seconds = wait_seconds
        if wait_seconds > 0 and response.created_at is not None:
            created = response.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            response.accept_available_at = created + timedelta(seconds=wait_seconds)


# 「申请取消中」筛选值：与 OrderStatus / ClaimLifecycleStatus 正交，
# 表示订单（或名额）存在待处理的取消协商（老板 2026-10-07 要求单独可筛）。
CANCEL_PENDING_FILTER = "CANCELLING"

# 「售后中」筛选值：与 OrderStatus / ClaimLifecycleStatus 正交，表示订单（我的派单）
# 或名额所属订单（我的接单）存在进行中（PENDING）的售后申请（老板 2026-10-18 要求）。
# 售后结案后自动不再命中，无需额外状态字段；与 CANCELLING 同一手法（伪值筛选）。
AFTER_SALE_FILTER = "AFTER_SALE"


def _resolve_order_status_filter(
    value: str | None,
) -> tuple[OrderStatus | None, bool, bool]:
    """把 status 查询参数解析成 (订单状态, 是否仅看待处理取消协商, 是否仅看售后中)。

    CANCELLING / AFTER_SALE 都不是订单状态枚举值，用后两个返回值单独表达；其余
    取值仍按 OrderStatus 严格校验，非法值返回中文 422（替代枚举自带的 422）。
    两个伪值互斥：只可能有一个为 True。
    """
    if not value:
        return None, False, False
    if value == CANCEL_PENDING_FILTER:
        return None, True, False
    if value == AFTER_SALE_FILTER:
        return None, False, True
    try:
        return OrderStatus(value), False, False
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"订单状态筛选值无效：{value}",
        )


def _resolve_claim_status_filter(
    value: str | None,
) -> tuple[ClaimLifecycleStatus | None, bool, bool]:
    """名额状态筛选解析：CANCELLING=该名额有取消协商挂起，AFTER_SALE=售后中，其余按枚举校验。"""
    if not value:
        return None, False, False
    if value == CANCEL_PENDING_FILTER:
        return None, True, False
    if value == AFTER_SALE_FILTER:
        return None, False, True
    try:
        return ClaimLifecycleStatus(value), False, False
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"接单状态筛选值无效：{value}",
        )


def _apply_slim_list_payload(response: OrderResponse) -> OrderResponse:
    """大厅轮询瘦身：剥掉列表卡片用不到的大字段，降低 JSON 体积与前端重绘成本。

    - description/description_raw/description_ai/notes/game_account/交付凭证
      在列表里从不展示，全部置空（详情页走单独接口拿全量）；
    - 附件只保留首图（大厅卡片的缩略图来源）；
    - intro 缺失时用 description_raw 前 40 字补位，摘要不因瘦身退化。
    """
    if not response.intro and response.description_raw:
        response.intro = response.description_raw[:40]
    response.description = None
    response.description_raw = None
    response.description_ai = None
    response.notes = None
    response.game_account = None
    response.delivery_attachments = None
    response.delivery_note = None
    if response.attachments and len(response.attachments) > 1:
        response.attachments = response.attachments[:1]
    return response


def _serialize_order(order, viewer: User) -> OrderResponse:
    """Serialize an order, redacting game_account from viewers who don't own
    it, weren't assigned to it, and aren't admins.
    Otherwise a booster browsing the PENDING list could harvest every
    user's game account.

    boss_contact（老板联系 ID）同样仅对 发布人/管理员/拥有该订单 OrderClaim 的打手可见；
    已报名打手的可见性由 _enrich_* 依据 my_claim 回填。
    """
    response = OrderResponse.model_validate(order)
    if viewer.role == UserRole.ADMIN:
        return response
    if viewer.id == order.user_id:
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
        response.pending_review_count = status_counts.get("PENDING_REVIEW", 0)
        response.settled_count = status_counts.get(ClaimLifecycleStatus.SETTLED.value, 0)

    if viewer.role != UserRole.ADMIN:
        claim_view = await order_service.get_order_claim_view(order, viewer)
        if claim_view is not None:
            response.my_claim = OrderClaimItem.model_validate(claim_view)
            # 已接单打手可见老板联系方式
            if response.boss_contact is None:
                response.boss_contact = order.boss_contact
        elif order.user_id != viewer.id:
            # Reset even if the incoming response was not serialized defensively.
            response.boss_contact = None
    pending_cancel_ids = await order_service.pending_cancel_order_ids([order.id])
    response.cancel_pending = order.id in pending_cancel_ids
    after_sale_ids = await order_service.pending_after_sale_order_ids([order.id])
    response.after_sale_pending = order.id in after_sale_ids
    await _apply_accept_window(db, [response], [order], viewer)
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
            response.pending_review_count = status_counts.get("PENDING_REVIEW", 0)
            response.settled_count = status_counts.get(
                ClaimLifecycleStatus.SETTLED.value, 0
            )

    if viewer.role != UserRole.ADMIN:
        claim_views = await order_service.claims_view_for_booster(orders, viewer)
        for response in responses:
            claim_view = claim_views.get(response.id)
            if claim_view is not None:
                response.my_claim = OrderClaimItem.model_validate(claim_view)
                # All claim viewers, including the first claimant, are restored here.
                if response.boss_contact is None:
                    response.boss_contact = orders_by_id[response.id].boss_contact
            elif orders_by_id[response.id].user_id != viewer.id:
                response.boss_contact = None
    # 「申请取消中」标记：整页一条 IN 查询，供前端把徽标/接单状态从
    # 进行中改显为申请取消中（取消协商挂起期间订单状态本身不变）。
    pending_cancel_ids = await order_service.pending_cancel_order_ids(
        [response.id for response in responses]
    )
    for response in responses:
        response.cancel_pending = response.id in pending_cancel_ids
    after_sale_ids = await order_service.pending_after_sale_order_ids(
        [response.id for response in responses]
    )
    for response in responses:
        response.after_sale_pending = response.id in after_sale_ids
    await _apply_accept_window(db, responses, orders, viewer)
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
    request: Request,
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

    async with measure_stage(request, STAGE_ORDER_SERVICE):
        order = await order_service.create_order(order_data, current_user)

    # 任何人发单：向所有活跃非管理员广播"新订单"通知（批量、单事务，排除自己）。
    # 卡片一律按「其他打手视角」构建：game_account/boss_contact 强制置空，
    # 不能把发布方（可能是管理员）视角的敏感字段推给全站打手。
    card_response = OrderResponse.model_validate(order).model_copy(
        update={"game_account": None, "boss_contact": None}
    )
    order_card = _apply_slim_list_payload(card_response).model_dump(mode="json")

    async with measure_stage(request, STAGE_ORDER_NOTIFICATIONS):
        await notify_boosters_new_order(
            db,
            order=order,
            exclude_user_id=current_user.id,
            request=request,
            order_card=order_card,
        )

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
        str | None,
        Query(
            alias="status",
            description="按订单状态筛选；CANCELLING=申请取消中（有待处理的取消协商）；AFTER_SALE=售后中（存在进行中的售后申请）",
        ),
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
    q: Annotated[
        str | None,
        Query(max_length=100, description="综合搜索：订单号精确命中优先，其次标题/游戏/需求内容"),
    ] = None,
    slim: Annotated[
        bool,
        Query(description="大厅轮询瘦身：剥掉列表卡片用不到的大字段"),
    ] = False,
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

    # 老板ID搜索仅用于"我的派单"（发布人查自己的单）与管理员；
    # 对普通打手关闭该过滤，防止把老板ID当作探测他人订单的条件。
    effective_boss_contact = (
        boss_contact
        if (mine_published or current_user.role == UserRole.ADMIN)
        else None
    )

    resolved_status, cancel_pending_only, after_sale_only = _resolve_order_status_filter(
        status_filter
    )
    orders, total, hall_max_updated_at, hall_earliest_deadline = await order_service.list_orders(
        user=current_user,
        game_name=game_name,
        status_filter=resolved_status,
        page=page,
        page_size=page_size,
        mine_published=mine_published,
        boss_contact=effective_boss_contact,
        q=q,
        cancel_pending_only=cancel_pending_only,
        after_sale_only=after_sale_only,
    )

    # Calculate total pages
    pages = (total + page_size - 1) // page_size if total > 0 else 0

    responses = [_serialize_order(order, current_user) for order in orders]
    responses = await _enrich_order_responses(db, responses, orders, current_user)
    if slim:
        responses = [_apply_slim_list_payload(response) for response in responses]

    return OrderListResponse(
        items=responses,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
        # 与 hall-digest 同口径的摘要基线，随列表一起下发（同一聚合查询顺带取回）。
        # 只在大厅口径下发：mine_published（我的派单）的计数范围与大厅不同，
        # 客户端拿它当基线会把错误口径的摘要混进对账判断。
        hall_count=total if not mine_published else None,
        hall_max_updated_at=hall_max_updated_at if not mine_published else None,
        hall_earliest_deadline=hall_earliest_deadline if not mine_published else None,
    )


@router.get(
    "/hall-digest",
    response_model=HallDigestResponse,
    summary="大厅对账摘要",
    description=(
        "轻量轮询：只返回当前可见订单的数量/最近更新时间/最早截止时间，"
        "客户端断线兜底轮询先拉摘要，无变化则跳过全量列表请求"
    ),
)
async def get_hall_digest(
    current_user: CurrentUser,
    db: DatabaseSession,
) -> HallDigestResponse:
    """大厅对账摘要：一条聚合查询代替全量列表，供客户端判断是否需要刷新。

    过滤条件与 list_orders 的 booster_scope 完全一致（非管理员只看可接/自己
    相关的订单，管理员不加过滤）：口径不同会让客户端「摘要没变就不拉全量」的
    判断失效（摘要不变但列表实际已变）。
    """
    order_service = get_order_service(db)
    count, max_updated_at, earliest_deadline = await order_service.hall_digest(current_user)
    # 顶部统计条的全量数字（2026-10-19 老板反馈「进行中 19 不准」）：
    # 前端以前在当页 20 条上 filter，翻页会变、也把待审核混进了进行中。
    stats = await order_service.hall_stats(current_user)
    return HallDigestResponse(
        count=count,
        max_updated_at=max_updated_at,
        earliest_deadline=earliest_deadline,
        pending_count=stats["pending"],
        active_count=stats["active"],
        pending_review_count=stats["pending_review"],
        completed_today=stats["completed_today"],
    )


@router.get(
    "/accept-captcha",
    response_model=AcceptCaptchaResponse,
    summary="获取接单图形验证码",
    description=(
        "后台开启「接单需要验证码」后可用：返回验证码ID与base64图片，"
        "有效期5分钟、一次性使用；与注册验证码按场景隔离"
    ),
)
async def get_accept_captcha(
    request: Request,
    db: DatabaseSession,
    current_user: Annotated[User, Depends(get_current_booster)],
) -> AcceptCaptchaResponse:
    """生成接单场景的图形验证码（仅非管理员打手可获取）。

    路由必须声明在 ``GET /{order_id}`` 之前，否则 ``accept-captcha`` 会被
    int 路径参数解析成 422。限流同时按「场景+用户」与「场景+IP」分桶：
    前者防单账号刷图，后者防同 IP 多账号刷图。
    """
    setting = await get_or_create_site_setting(db)
    if not setting.accept_captcha_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="接单验证码已关闭",
        )

    ip = client_ip_from_request(request)
    accept_limit = captcha_service.rate_limit_for("accept")
    if not captcha_service.check_rate_limit(
        captcha_service.user_rate_key("accept", current_user.id), accept_limit
    ) or not captcha_service.check_rate_limit(
        captcha_service.scene_rate_key("accept", ip), accept_limit
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "captcha_rate_limited",
                "message": "当前抢单人数较多，请稍候再试",
                "retry_after": 3,
            },
        )

    # PIL 图片生成放线程池，避免多人同时打开接单弹窗时阻塞事件循环
    captcha_id, png_bytes = await anyio.to_thread.run_sync(
        captcha_service.create, "accept"
    )
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return AcceptCaptchaResponse(
        captcha_id=captcha_id,
        image=f"data:image/png;base64,{b64}",
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
    data, suffix, content_type = await validate_image_upload(attachment, max_size_bytes=10 * 1024 * 1024)
    attachment_name = Path(attachment.filename or "attachment").name
    if not attachment_name or len(attachment_name) > 255:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="附件文件名无效")
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
    data_bytes, suffix, content_type = await validate_image_upload(attachment, max_size_bytes=10 * 1024 * 1024)
    attachment_name = Path(attachment.filename or "attachment").name
    if not attachment_name or len(attachment_name) > 255:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="附件文件名无效")
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
    response_model=OrderDeliveryAttachmentsResponse,
    summary="删除交付附件",
)
async def delete_deliver_attachment(
    order_id: int,
    attachment_index: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> OrderDeliveryAttachmentsResponse:
    """Delete a delivery proof image by index from the caller's own claim."""
    if current_user.role == UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="管理员不参与交付附件")
    order_service = get_order_service(db)
    order, claim = await order_service.get_my_claim_for_delivery(order_id, current_user)

    items = _delivery_attachment_items(claim.delivery_attachments)
    if attachment_index < 0 or attachment_index >= len(items):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="附件索引越界")
    item = items.pop(attachment_index)
    if not item.url.startswith("/uploads/deliveries/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="附件路径无效")
    relative = item.url.removeprefix("/uploads/")
    upload_root = Path(settings.UPLOAD_DIR).resolve()
    file_path = (upload_root / relative).resolve()
    if upload_root not in file_path.parents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="附件路径无效")
    claim.delivery_attachments = [entry.model_dump() for entry in items]
    await db.flush()
    await run_after_commit(lambda: _cleanup_rejected_delivery_files([item.url]))
    return OrderDeliveryAttachmentsResponse(delivery_attachments=items)


@router.get(
    "/claims/mine",
    response_model=MyOrderClaimListResponse,
    summary="我的报名记录",
    description="分页获取当前用户的订单报名记录（名额），按记录ID倒序，支持按状态与老板ID筛选",
)
async def list_my_claims(
    current_user: CurrentUser,
    db: DatabaseSession,
    status_filter: Annotated[
        str | None,
        Query(
            alias="status",
            description="按名额状态筛选：CLAIMED/DELIVERED/SETTLED/CANCELLED；CANCELLING=该名额有取消协商挂起（申请取消中）；AFTER_SALE=售后中（该名额所属订单存在进行中的售后申请）",
        ),
    ] = None,
    page: Annotated[
        int,
        Query(ge=1, description="页码"),
    ] = 1,
    page_size: Annotated[
        int,
        Query(ge=1, le=100, description="每页数量"),
    ] = 20,
    q: Annotated[
        str | None,
        Query(max_length=100, description="搜索订单号、接单记录号、标题、游戏或需求内容"),
    ] = None,
    boss_contact: Annotated[
        str | None,
        Query(description="按老板ID模糊筛选（打手检索自己接过谁的活）", max_length=64),
    ] = None,
) -> MyOrderClaimListResponse:
    """
    List the current user's claims (我的报名) with the parent order summary.

    - Non-admin accounts only (admins do not claim orders)
    - Ordered by claim id descending (newest first)
    - Each item carries the claim lifecycle fields plus an ``order`` summary
    - ``q`` accepts the parent order ID as the canonical number and also the
      claim record ID as a compatibility alias
    - ``boss_contact`` filters own claims by the boss contact ID (partial
      match); safe because the list only contains the caller's own claims
    """
    if current_user.role == UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="管理员不参与报名，无法查看报名记录",
        )

    order_service = get_order_service(db)
    resolved_claim_status, cancel_pending_only, after_sale_only = (
        _resolve_claim_status_filter(status_filter)
    )
    items, total = await order_service.list_my_claims(
        current_user.id,
        status_filter=resolved_claim_status,
        page=page,
        page_size=page_size,
        q=q,
        boss_contact=boss_contact,
        cancel_pending_only=cancel_pending_only,
        after_sale_only=after_sale_only,
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
    response = await _enrich_order_response(db, response, order, current_user)
    # 接单验证码开关由服务端回填，详情页据此决定点「接手」是否先弹验证码。
    # 只在详情接口查询设置，列表接口保持默认 False（避免每页多一次单行查询）。
    setting = await get_or_create_site_setting(db)
    response.accept_captcha_required = bool(setting.accept_captcha_enabled)
    return response


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
    description="订单发布人或管理员可通过交付，或驳回并要求打手重新上传",
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

    - Order publisher or ADMIN can review a DELIVERED claim
    - action=approve settles the payout; action=reject requires a reason and lets
      the booster replace the rejected submission
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
        rejection_reason=payload.reason,
    )
    rejected_urls = claim.pop("_rejected_attachment_urls", [])
    if rejected_urls:
        await run_after_commit(
            lambda: _cleanup_rejected_delivery_files(rejected_urls)
        )
    if payload.action == "reject":
        await notify_user(
            db,
            user_id=claim["booster_id"],
            type=NotificationType.ORDER_DELIVERED,
            title="订单交付被驳回",
            content=f"交付内容被驳回：{payload.reason}。请按原因修改后重新提交。",
            link=f"/orders/{order_id}",
            ref_id=order_id,
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
    request: Request,
    payload: OrderAcceptRequest | None = None,
) -> OrderResponse:
    """
    Accept an order as a booster.

    - Any non-admin authenticated user can accept orders
    - Only PENDING orders can be accepted
    - Cannot accept your own order
    - Admin state changes go through /admin/orders/{id}/intervene, not here
    - 后台开启「接单需要验证码」时必须携带 captcha_id/captcha_code；
      关闭时保持旧契约（可不带请求体）
    """
    order_service = get_order_service(db)

    # 验证码闸门放在 accept_order 之前：避免校验失败时先改动任何订单/名额数据。
    setting = await get_or_create_site_setting(db)
    if setting.accept_captcha_enabled:
        if (
            payload is None
            or not payload.captcha_id
            or not payload.captcha_code
            or not captcha_service.verify(
                payload.captcha_id, payload.captcha_code, "accept"
            )
        ):
            raise _accept_captcha_invalid()

    async with measure_stage(request, STAGE_ACCEPT_SERVICE):
        order = await order_service.accept_order(order_id, current_user)

    async with measure_stage(request, STAGE_ACCEPT_SIDE_EFFECTS):
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
    await broadcast_order_state_changed(
        order_id=order.id,
        status=order.status,
        claim_status=order.claim_status,
        claimed_count=order.claimed_count,
    )

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
    await broadcast_order_state_changed(
        order_id=order.id,
        status=order.status,
        claim_status=order.claim_status,
        claimed_count=order.claimed_count,
    )

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

    await broadcast_order_state_changed(
        order_id=order.id,
        status=order.status,
        claim_status=order.claim_status,
        claimed_count=order.claimed_count,
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

    await broadcast_order_state_changed(
        order_id=order.id,
        status=order.status,
        claim_status=order.claim_status,
        claimed_count=order.claimed_count,
    )

    return OrderResponse.model_validate(order)


@router.post(
    "/{order_id}/cancel-requests",
    response_model=OrderCancelRequestItem,
    summary="发起单名额取消协商",
    description="发单员或该名额打手提出理由与赔偿金额，等待对方同意或拒绝；不经过管理员。",
)
async def create_order_cancel_request(
    order_id: int,
    payload: CreateOrderCancelRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> OrderCancelRequestItem:
    service = get_order_service(db)
    request = await service.create_cancel_request(
        order_id,
        current_user,
        claim_id=payload.claim_id,
        reason=payload.reason,
        compensation_amount=payload.compensation_amount,
    )
    order = await service.get_order_by_id(order_id, current_user)
    visible_items = await service.list_cancel_requests(order_id, current_user)
    item = next((entry for entry in visible_items if int(entry["id"]) == request.id), None)
    if item is None:
        raise HTTPException(status_code=500, detail="取消申请已创建但读取失败，请重新打开订单")

    if request.requester_role == "PUBLISHER":
        title = "发单员申请取消待你确认"
        content = (
            f"发单员对订单「{order.game_name}」的该接单名额提出取消协商，"
            f"拟从该打手扣除 ¥{request.compensation_amount} 补偿发单员"
            f"（按冻结赔付→可用余额→保证金顺序）。原因：{request.reason}"
        )
    else:
        title = "打手申请取消待你确认"
        content = (
            f"打手对订单「{order.game_name}」提出取消协商，"
            f"愿意赔偿发单员 ¥{request.compensation_amount}"
            f"（按冻结赔付→可用余额→保证金顺序）。原因：{request.reason}"
        )
    await send_order_system_message(
        db=db,
        order_id=order.id,
        content=f"{current_user.username} 发起了取消协商，等待对方同意或拒绝",
        meta_json={
            "event": "order_cancel_requested",
            "order_id": order.id,
            "claim_id": request.claim_id,
            "cancel_request_id": request.id,
            "operator_id": current_user.id,
            "reason": request.reason,
            "compensation_amount": str(request.compensation_amount),
        },
    )
    await notify_user(
        db,
        user_id=request.recipient_id,
        type=NotificationType.ORDER_CANCEL_REQUESTED,
        title=title,
        content=content,
        link=f"/orders/{order.id}",
        ref_id=order.id,
    )
    return OrderCancelRequestItem.model_validate(item)


@router.get(
    "/{order_id}/cancel-requests",
    response_model=OrderCancelRequestListResponse,
    summary="查看订单取消协商",
)
async def list_order_cancel_requests(
    order_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> OrderCancelRequestListResponse:
    items = await get_order_service(db).list_cancel_requests(order_id, current_user)
    return OrderCancelRequestListResponse(
        items=[OrderCancelRequestItem.model_validate(item) for item in items]
    )


@router.post(
    "/{order_id}/cancel-requests/{request_id}/decision",
    response_model=OrderCancelRequestItem,
    summary="同意或拒绝取消协商",
    description="仅申请的另一方可以同意或拒绝；同意只取消该接单名额。",
)
async def decide_order_cancel_request(
    order_id: int,
    request_id: int,
    payload: DecideOrderCancelRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> OrderCancelRequestItem:
    service = get_order_service(db)
    request = await service.decide_cancel_request(
        order_id,
        request_id,
        current_user,
        action=payload.action,
        decision_reason=payload.decision_reason,
    )
    order = await service.get_order_by_id(order_id, current_user)
    visible_items = await service.list_cancel_requests(order_id, current_user)
    item = next((entry for entry in visible_items if int(entry["id"]) == request.id), None)
    if item is None:
        raise HTTPException(status_code=500, detail="取消协商处理成功但读取失败，请重新打开订单")

    if request.status == OrderCancelRequestStatus.APPROVED:
        outcome = "双方已同意取消该接单名额"
        if request.compensation_amount > Decimal("0"):
            outcome += f"，赔偿 ¥{request.compensation_amount} 已划转"
        for user_id in sorted({request.requester_id, request.recipient_id}):
            await notify_user(
                db,
                user_id=user_id,
                type=NotificationType.ORDER_CANCEL_REQUEST_RESOLVED,
                title="取消协商已同意",
                content=f"订单「{order.game_name}」：{outcome}",
                link=f"/orders/{order.id}",
                ref_id=order.id,
            )
        await send_order_system_message(
            db=db,
            order_id=order.id,
            content=f"取消协商已同意：订单名额 #{request.claim_id} 已取消",
            meta_json={
                "event": "order_cancel_request_approved",
                "order_id": order.id,
                "claim_id": request.claim_id,
                "cancel_request_id": request.id,
                "operator_id": current_user.id,
                "compensation_amount": str(request.compensation_amount),
            },
        )
        await broadcast_order_state_changed(
            order_id=order.id,
            status=order.status,
            claim_status=order.claim_status,
            claimed_count=order.claimed_count,
        )
    elif request.status == OrderCancelRequestStatus.REJECTED:
        reason_text = f"；对方备注：{request.decision_reason}" if request.decision_reason else ""
        await notify_user(
            db,
            user_id=request.requester_id,
            type=NotificationType.ORDER_CANCEL_REQUEST_RESOLVED,
            title="取消申请被拒绝",
            content=f"订单「{order.game_name}」的取消协商被对方拒绝{reason_text}，订单状态未改变",
            link=f"/orders/{order.id}",
            ref_id=order.id,
        )
        await send_order_system_message(
            db=db,
            order_id=order.id,
            content="取消协商被对方拒绝，订单与名额维持原状态",
            meta_json={
                "event": "order_cancel_request_rejected",
                "order_id": order.id,
                "claim_id": request.claim_id,
                "cancel_request_id": request.id,
                "operator_id": current_user.id,
                "decision_reason": request.decision_reason,
            },
        )
    else:
        await notify_user(
            db,
            user_id=request.requester_id,
            type=NotificationType.ORDER_CANCEL_REQUEST_RESOLVED,
            title="取消申请已关闭",
            content=f"订单「{order.game_name}」状态已变化，该取消申请已关闭",
            link=f"/orders/{order.id}",
            ref_id=order.id,
        )
    return OrderCancelRequestItem.model_validate(item)


@router.post(
    "/{order_id}/apply-cancel",
    response_model=OrderResponse,
    summary="旧版发单员直接取消接口（已停用）",
    description="取消流程已升级为双方协商，请刷新页面后重新发起。",
)
async def apply_cancel_order(
    order_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> OrderResponse:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="取消流程已升级为双方同意/拒绝，请刷新页面后重新发起",
    )


@router.post(
    "/{order_id}/request-cancel",
    response_model=OrderResponse,
    summary="旧版打手管理员裁决取消接口（已停用）",
    description="取消流程已升级为双方协商，不再经过管理员；请刷新页面。",
)
async def request_cancel_order(
    order_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> OrderResponse:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="取消流程已升级为双方同意/拒绝，请刷新页面后重新发起",
    )


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

    await broadcast_order_state_changed(
        order_id=order.id,
        status=order.status,
        claim_status=order.claim_status,
        claimed_count=order.claimed_count,
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
    await broadcast_order_state_changed(
        order_id=order.id,
        status=order.status,
        claim_status=order.claim_status,
        claimed_count=order.claimed_count,
    )
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
