"""售后申请 API。

老板（订单发布者）对已完成/已取消订单发起/查询售后、上传凭证图片、查看「我的售后」、
撤回待处理售后；该单打手可一键认栽秒结（同意退款，不等管理员审阅）；
管理员在后台审阅并通过/驳回/关闭。

路径约定与取消协商（/orders/{id}/cancel-requests）保持一致：订单维度的售后
挂在 /orders 下，个人维度走 /after-sales，后台走 /admin/after-sales。
"""

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DatabaseSession, get_current_admin
from app.models.order import Order
from app.models.order_after_sale import OrderAfterSale
from app.models.user import User, UserRole
from app.schemas.after_sale import (
    AdminAfterSaleItem,
    AdminAfterSaleListResponse,
    AfterSaleImageOut,
    AfterSaleItem,
    CreateAfterSaleRequest,
    ReviewAfterSaleRequest,
)
from app.services.after_sale_service import (
    accept_after_sale_as_booster,
    admin_list_after_sales,
    create_after_sale,
    get_after_sale_item,
    list_my_after_sales,
    list_order_after_sales,
    review_after_sale,
    withdraw_after_sale,
)
from app.services.file_service import save_image_bytes, validate_image_upload

logger = logging.getLogger(__name__)

after_sales_router = APIRouter(tags=["售后"])
admin_after_sales_router = APIRouter(prefix="/admin/after-sales", tags=["售后管理"])


def _is_order_participant(order: Order, user: User) -> bool:
    """发布者、该单打手或管理员可见/可操作该单售后。"""
    return user.role == UserRole.ADMIN or user.id in (order.user_id, order.booster_id)


async def _get_order_or_404(db: DatabaseSession, order_id: int) -> Order:
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="订单不存在")
    return order


@after_sales_router.post(
    "/orders/{order_id}/after-sales",
    response_model=AfterSaleItem,
    status_code=status.HTTP_201_CREATED,
    summary="发起售后申请",
    description="仅订单发布者可对已完成或已取消订单发起售后（原因 5-500 字 + 最多 9 张凭证 + 金额 ≤ 派单价）",
)
async def create_order_after_sale(
    order_id: int,
    payload: CreateAfterSaleRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> AfterSaleItem:
    # 锁订单行：与「已完成」状态变更、并发重复发起互斥
    result = await db.execute(select(Order).where(Order.id == order_id).with_for_update())
    order = result.scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="订单不存在")
    after_sale = await create_after_sale(
        db,
        order=order,
        requester=current_user,
        reason=payload.reason,
        amount=payload.amount,
        images=payload.images,
    )
    item = await get_after_sale_item(db, after_sale.id)
    return AfterSaleItem.model_validate(item)


@after_sales_router.get(
    "/orders/{order_id}/after-sales",
    response_model=list[AfterSaleItem],
    summary="订单售后记录",
    description="该单全部售后记录（创建时间倒序）；仅发布者/该单打手/管理员可看",
)
async def list_order_after_sale_records(
    order_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> list[AfterSaleItem]:
    order = await _get_order_or_404(db, order_id)
    if not _is_order_participant(order, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有订单发布者、接单打手或管理员可以查看售后记录",
        )
    items = await list_order_after_sales(db, order_id)
    return [AfterSaleItem.model_validate(item) for item in items]


@after_sales_router.post(
    "/orders/{order_id}/after-sale-images",
    response_model=AfterSaleImageOut,
    status_code=status.HTTP_201_CREATED,
    summary="上传售后凭证图片",
    description="单张 <=10MB 的 png/jpeg/webp，保存到上传者本人目录，URL 随后随售后申请提交",
)
async def upload_after_sale_image(
    order_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
    image: UploadFile = File(..., description="售后凭证图片"),
) -> AfterSaleImageOut:
    """上传售后凭证（仿交付附件，但不限订单状态、不挂 claim）。

    仅订单参与方（发布者/该单打手/管理员）可上传；文件落在
    uploads/aftersales/{user_id}/ 下，创建售后时按归属校验，避免引用
    他人目录的图片。
    """
    order = await _get_order_or_404(db, order_id)
    if not _is_order_participant(order, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有订单发布者、接单打手或管理员可以上传售后凭证",
        )
    data_bytes, suffix, content_type = await validate_image_upload(
        image, max_size_bytes=10 * 1024 * 1024
    )
    attachment_name = Path(image.filename or "image").name
    if not attachment_name or len(attachment_name) > 255:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="凭证文件名无效"
        )
    url = save_image_bytes(data_bytes, suffix, f"aftersales/{current_user.id}")
    return AfterSaleImageOut(
        url=url,
        name=attachment_name,
        size=len(data_bytes),
        content_type=content_type,
    )


@after_sales_router.get(
    "/after-sales/mine",
    response_model=list[AfterSaleItem],
    summary="我的售后列表",
    description="role=requester（老板视角，默认）/ booster（打手视角），可按状态筛选",
)
async def list_my_after_sale_records(
    current_user: CurrentUser,
    db: DatabaseSession,
    role: Annotated[
        str,
        Query(pattern="^(requester|booster)$", description="视角：requester=老板，booster=打手"),
    ] = "requester",
    status_filter: Annotated[
        str | None,
        Query(alias="status", description="按售后状态筛选：PENDING/APPROVED/REJECTED/CLOSED"),
    ] = None,
) -> list[AfterSaleItem]:
    items = await list_my_after_sales(
        db, current_user.id, role=role, status=status_filter
    )
    return [AfterSaleItem.model_validate(item) for item in items]


@admin_after_sales_router.get(
    "",
    response_model=AdminAfterSaleListResponse,
    summary="售后申请列表（后台）",
    description="按状态/订单/关键词筛选 + 分页，创建时间倒序",
)
async def list_after_sales_for_admin(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    status_filter: Annotated[
        str | None,
        Query(alias="status", description="按售后状态筛选：PENDING/APPROVED/REJECTED/CLOSED"),
    ] = None,
    order_id: Annotated[int | None, Query(description="按订单 ID 筛选")] = None,
    q: Annotated[
        str | None,
        Query(max_length=100, description="综合搜索：订单号精确命中优先，其次订单标题/发布者/打手用户名"),
    ] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> AdminAfterSaleListResponse:
    items, total = await admin_list_after_sales(
        db,
        status=status_filter,
        order_id=order_id,
        q=q,
        page=page,
        page_size=page_size,
    )
    pages = (total + page_size - 1) // page_size if total > 0 else 0
    return AdminAfterSaleListResponse(
        items=[AdminAfterSaleItem.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@after_sales_router.post(
    "/orders/{order_id}/after-sales/{after_sale_id}/withdraw",
    response_model=AfterSaleItem,
    summary="撤回售后申请",
    description="仅售后发布者本人可撤回待处理（PENDING）售后；撤回不动资金，打手提现限制解除",
)
async def withdraw_order_after_sale(
    order_id: int,
    after_sale_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> AfterSaleItem:
    """老板撤诉：行锁售后单后交给 service 校验身份与状态（仅本人、仅 PENDING）。"""
    result = await db.execute(
        select(OrderAfterSale)
        .where(
            OrderAfterSale.id == after_sale_id,
            OrderAfterSale.order_id == order_id,
        )
        .with_for_update()
    )
    after_sale = result.scalar_one_or_none()
    if after_sale is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="售后申请不存在"
        )
    await withdraw_after_sale(db, after_sale=after_sale, user=current_user)
    item = await get_after_sale_item(db, after_sale.id)
    return AfterSaleItem.model_validate(item)


@after_sales_router.post(
    "/orders/{order_id}/after-sales/{after_sale_id}/accept",
    response_model=AfterSaleItem,
    summary="打手同意退款",
    description=(
        "仅该单接单打手可对待处理（PENDING）售后一键认栽：按申请金额秒结扣划，"
        "先从可用余额扣、不足部分从保证金补扣；两处合计仍不足时返回 400 并告知"
        "缺口金额（售后保持待处理、资金零变动），无需管理员审阅"
    ),
)
async def accept_order_after_sale(
    order_id: int,
    after_sale_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> AfterSaleItem:
    """打手认栽秒结：行锁售后单、校验订单归属后交给 service 判身份与状态
    （仅该订单的打手、仅 PENDING），回显结构与撤诉端点一致。"""
    result = await db.execute(
        select(OrderAfterSale).where(OrderAfterSale.id == after_sale_id).with_for_update()
    )
    after_sale = result.scalar_one_or_none()
    if after_sale is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="售后申请不存在"
        )
    if after_sale.order_id != order_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="售后申请不属于该订单",
        )
    await accept_after_sale_as_booster(db, after_sale=after_sale, booster=current_user)
    item = await get_after_sale_item(db, after_sale.id)
    return AfterSaleItem.model_validate(item)


@admin_after_sales_router.post(
    "/{after_sale_id}/review",
    response_model=AfterSaleItem,
    summary="审阅售后申请",
    description=(
        "approve=通过（可传 amount 调裁定扣款额，默认=申请金额；扣款顺序为"
        "「打手可用余额 → 保证金」，合计不足时返回 400 且不结案）；"
        "reject=驳回、close=关闭（平台未做裁定），两者必须填原因"
    ),
)
async def review_after_sale_record(
    after_sale_id: int,
    payload: ReviewAfterSaleRequest,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> AfterSaleItem:
    result = await db.execute(
        select(OrderAfterSale).where(OrderAfterSale.id == after_sale_id).with_for_update()
    )
    after_sale = result.scalar_one_or_none()
    if after_sale is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="售后申请不存在"
        )
    await review_after_sale(
        db,
        after_sale=after_sale,
        admin=current_admin,
        action=payload.action,
        reason=payload.reason,
        amount=payload.amount,
    )
    item = await get_after_sale_item(db, after_sale.id)
    return AfterSaleItem.model_validate(item)
