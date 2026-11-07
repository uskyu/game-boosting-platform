"""User applications for publishing (发单) permission.

一套代码服务多站：新站默认只允许接单，用户可自助申请发单权限，由管理员
审核。每次申请保留一条记录（含历史被驳回/撤回的申请），便于后台查看历史。
"""

from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class PublishPermissionStatus(str, PyEnum):
    """Lifecycle of a publish-permission application."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class PublishPermissionRequest(Base):
    """One application for permission to publish orders."""

    __tablename__ = "publish_permission_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[PublishPermissionStatus] = mapped_column(
        Enum(
            PublishPermissionStatus,
            name="publish_permission_status_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=PublishPermissionStatus.PENDING,
        server_default="PENDING",
        nullable=False,
        index=True,
    )
    decision_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), default=func.now(), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    def __repr__(self) -> str:
        return (
            "<PublishPermissionRequest("
            f"id={self.id}, user_id={self.user_id}, status={self.status.value}"
            ")>"
        )
