"""
Notification and user preference schemas.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.notification import NotificationType

# =============================================================================
# Notification schemas
# =============================================================================

class NotificationResponse(BaseModel):
    """Single notification item."""

    id: int = Field(description="通知ID")
    user_id: int = Field(description="用户ID")
    type: NotificationType = Field(description="通知类型")
    title: str = Field(description="标题")
    content: str = Field(description="内容")
    link: str | None = Field(default=None, description="跳转链接")
    ref_id: int | None = Field(default=None, description="关联实体ID")
    is_read: bool = Field(description="是否已读")
    created_at: datetime = Field(description="创建时间")
    read_at: datetime | None = Field(default=None, description="阅读时间")

    model_config = ConfigDict(from_attributes=True)


class NotificationListResponse(BaseModel):
    """Paginated notification list."""

    items: list[NotificationResponse] = Field(description="通知列表")
    total: int = Field(description="总数")
    unread_count: int = Field(description="未读数")


class NotificationUnreadCount(BaseModel):
    """Unread notification count."""

    count: int = Field(description="未读通知数")


# =============================================================================
# User preference schemas
# =============================================================================

class UserPreferenceResponse(BaseModel):
    """User preference / settings response."""

    notification_settings: dict | None = Field(default=None, description="通知偏好")
    profile_visible: bool = Field(description="资料公开")
    show_online_status: bool = Field(description="显示在线状态")
    language: str = Field(description="语言")

    model_config = ConfigDict(from_attributes=True)


class UserPreferenceUpdate(BaseModel):
    """Request body for updating user preferences."""

    notification_settings: dict | None = Field(default=None, description="通知偏好设置")
    profile_visible: bool | None = Field(default=None, description="资料公开")
    show_online_status: bool | None = Field(default=None, description="显示在线状态")
    language: str | None = Field(default=None, description="语言")
