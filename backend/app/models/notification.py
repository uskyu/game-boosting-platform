"""
Notification and user preference models.
Supports platform-wide notification center and per-user settings.
"""

from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import JSON, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class NotificationType(str, PyEnum):
    """Types of notifications the platform can generate."""

    ORDER_ACCEPTED = "ORDER_ACCEPTED"           # 订单被接单
    ORDER_DELIVERED = "ORDER_DELIVERED"          # 代练提交完成
    ORDER_CONFIRMED = "ORDER_CONFIRMED"          # 客户确认完成
    ORDER_DISPUTED = "ORDER_DISPUTED"            # 订单争议
    ORDER_CANCELLED = "ORDER_CANCELLED"          # 订单取消
    NEW_MESSAGE = "NEW_MESSAGE"                  # 新聊天消息
    APPLICATION_APPROVED = "APPLICATION_APPROVED"  # 代练申请通过
    APPLICATION_REJECTED = "APPLICATION_REJECTED"  # 代练申请拒绝
    REVIEW_RECEIVED = "REVIEW_RECEIVED"          # 收到评价
    SYSTEM_ANNOUNCEMENT = "SYSTEM_ANNOUNCEMENT"  # 系统公告


class Notification(Base):
    """
    Notification record targeting a specific user.
    Created by backend services when notable events occur.
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    type: Mapped[NotificationType] = mapped_column(
        Enum(
            NotificationType,
            name="notification_type_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Optional link for click-through (e.g. /orders/123)
    link: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    # Optional reference to the entity that triggered the notification
    ref_id: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    is_read: Mapped[bool] = mapped_column(
        default=False,
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    read_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<Notification(id={self.id}, user_id={self.user_id}, type={self.type.value})>"


class UserPreference(Base):
    """
    Per-user settings: notification toggles, privacy, display preferences.
    One row per user, created lazily on first settings access.
    """

    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Notification toggles (JSON dict of NotificationType -> bool)
    notification_settings: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # Privacy settings
    profile_visible: Mapped[bool] = mapped_column(
        default=True,
        nullable=False,
    )

    show_online_status: Mapped[bool] = mapped_column(
        default=True,
        nullable=False,
    )

    # Display preferences
    language: Mapped[str] = mapped_column(
        String(10),
        default="zh-CN",
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<UserPreference(id={self.id}, user_id={self.user_id})>"
