"""
Notification and user preference models.
Supports platform-wide notification center and per-user settings.
"""

from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import JSON, Enum, ForeignKey, Index, String, Text
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
    ORDER_CANCEL_REQUESTED = "ORDER_CANCEL_REQUESTED"  # 对方发起取消申请，待我处理
    ORDER_CANCEL_REQUEST_RESOLVED = "ORDER_CANCEL_REQUEST_RESOLVED"  # 取消申请已同意/拒绝
    AFTERSALES_CREATED = "AFTERSALES_CREATED"        # 老板发起售后申请，待平台裁定
    AFTERSALES_RESOLVED = "AFTERSALES_RESOLVED"      # 售后申请已通过/驳回
    RECHARGE_UPDATED = "RECHARGE_UPDATED"            # 充值记录被管理员人工改判成功/失败


class Notification(Base):
    """
    Notification record targeting a specific user.
    Created by backend services when notable events occur.
    """

    __tablename__ = "notifications"
    # 轮询热路径（WS 断线时客户端每 2s 拉一次）：列表 = user_id 等值 +
    # created_at 倒序。035 已有 (user_id, is_read) 覆盖未读计数，这里补列表用的
    # (user_id, created_at)，让 ORDER BY 走有序前缀而不是 filesort 全量历史。
    __table_args__ = (
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )

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
