"""Notification and user preference endpoints."""

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DatabaseSession
from app.schemas.notification import (
    NotificationListResponse,
    NotificationResponse,
    NotificationUnreadCount,
    UserPreferenceResponse,
    UserPreferenceUpdate,
)
from app.services.notification_service import get_notification_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    db: DatabaseSession,
    current_user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    unread_only: bool = Query(default=False),
) -> NotificationListResponse:
    """获取当前用户的通知列表。"""
    svc = get_notification_service(db)
    items, total, unread_count = await svc.list_for_user(
        current_user.id,
        page=page,
        page_size=page_size,
        unread_only=unread_only,
    )
    return NotificationListResponse(
        items=[NotificationResponse.model_validate(n) for n in items],
        total=total,
        unread_count=unread_count,
    )


@router.get("/unread-count", response_model=NotificationUnreadCount)
async def get_unread_count(
    db: DatabaseSession,
    current_user: CurrentUser,
) -> NotificationUnreadCount:
    """获取未读通知数量（用于导航栏徽章）。"""
    svc = get_notification_service(db)
    count = await svc.get_unread_count(current_user.id)
    return NotificationUnreadCount(count=count)


@router.post("/{notification_id}/read", response_model=NotificationResponse)
async def mark_notification_read(
    notification_id: int,
    db: DatabaseSession,
    current_user: CurrentUser,
) -> NotificationResponse:
    """标记单条通知已读。"""
    svc = get_notification_service(db)
    notification = await svc.mark_read(notification_id, current_user.id)
    if notification is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="通知不存在",
        )
    await db.commit()
    return NotificationResponse.model_validate(notification)


@router.post("/read-all", status_code=status.HTTP_200_OK)
async def mark_all_read(
    db: DatabaseSession,
    current_user: CurrentUser,
) -> dict:
    """标记所有通知已读。"""
    svc = get_notification_service(db)
    count = await svc.mark_all_read(current_user.id)
    await db.commit()
    return {"message": f"已标记 {count} 条通知为已读", "count": count}


# =============================================================================
# User preferences / settings
# =============================================================================

@router.get("/settings", response_model=UserPreferenceResponse)
async def get_settings(
    db: DatabaseSession,
    current_user: CurrentUser,
) -> UserPreferenceResponse:
    """获取用户偏好设置。"""
    svc = get_notification_service(db)
    pref = await svc.get_preferences(current_user.id)
    await db.commit()
    return UserPreferenceResponse.model_validate(pref)


@router.put("/settings", response_model=UserPreferenceResponse)
async def update_settings(
    body: UserPreferenceUpdate,
    db: DatabaseSession,
    current_user: CurrentUser,
) -> UserPreferenceResponse:
    """更新用户偏好设置。"""
    svc = get_notification_service(db)
    pref = await svc.update_preferences(
        current_user.id,
        notification_settings=body.notification_settings,
        profile_visible=body.profile_visible,
        show_online_status=body.show_online_status,
        language=body.language,
    )
    await db.commit()
    return UserPreferenceResponse.model_validate(pref)
