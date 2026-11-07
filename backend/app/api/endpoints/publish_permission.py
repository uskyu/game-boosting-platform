"""User self-service and administrator review endpoints for publish permission."""

from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Query

from app.api.deps import CurrentUser, DatabaseSession, get_current_admin
from app.models.publish_permission_request import PublishPermissionStatus
from app.models.user import User
from app.schemas.publish_permission import (
    AdminPublishPermissionListResponse,
    PublishPermissionApplyRequest,
    PublishPermissionMineResponse,
    PublishPermissionRequestItem,
    PublishPermissionReviewRequest,
)
from app.services import publish_permission_service

user_router = APIRouter(prefix="/publish-permission", tags=["发单权限"])
admin_router = APIRouter(
    prefix="/admin/publish-permission", tags=["admin-publish-permission"]
)


@user_router.get("/mine", response_model=PublishPermissionMineResponse, summary="我的发单权限状态")
async def get_my_publish_permission(
    db: DatabaseSession,
    current_user: CurrentUser,
) -> PublishPermissionMineResponse:
    """返回当前用户能否发单、是否允许申请，以及最近一次申请记录。"""
    return await publish_permission_service.get_mine(db, current_user)


@user_router.post(
    "/apply",
    response_model=PublishPermissionRequestItem,
    summary="申请发单权限",
)
async def apply_publish_permission(
    db: DatabaseSession,
    current_user: CurrentUser,
    payload: Annotated[PublishPermissionApplyRequest | None, Body()] = None,
) -> PublishPermissionRequestItem:
    """提交一条待审核的发单权限申请；理由可选。"""
    reason = payload.reason if payload is not None else None
    return await publish_permission_service.apply(db, current_user, reason)


@user_router.post(
    "/{request_id}/withdraw",
    response_model=PublishPermissionRequestItem,
    summary="撤回发单权限申请",
)
async def withdraw_publish_permission(
    request_id: int,
    db: DatabaseSession,
    current_user: CurrentUser,
) -> PublishPermissionRequestItem:
    """撤回本人仍在待审核状态的申请。"""
    return await publish_permission_service.withdraw(db, current_user, request_id)


@admin_router.get(
    "/requests",
    response_model=AdminPublishPermissionListResponse,
    summary="发单权限申请列表",
)
async def list_publish_permission_requests(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    status_filter: Annotated[
        PublishPermissionStatus | None,
        Query(alias="status", description="按申请状态筛选"),
    ] = None,
    q: Annotated[
        str | None,
        Query(max_length=100, description="搜索用户名、邮箱、申请理由或用户 ID"),
    ] = None,
    page: Annotated[int, Query(ge=1, le=100000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AdminPublishPermissionListResponse:
    items, total = await publish_permission_service.list_admin(
        db, status_filter, q, page, page_size
    )
    pages = (total + page_size - 1) // page_size if total else 0
    return AdminPublishPermissionListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@admin_router.post(
    "/requests/{request_id}/review",
    response_model=PublishPermissionRequestItem,
    summary="审核发单权限申请",
)
async def review_publish_permission_request(
    request_id: int,
    payload: PublishPermissionReviewRequest,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> PublishPermissionRequestItem:
    """approve 开通发单权限并通知申请人；reject 必须填写原因。"""
    action: Literal["approve", "reject"] = payload.action
    return await publish_permission_service.review(
        db, current_admin, request_id, action, payload.reason
    )
