"""用户登录会话（设备）表。

每个浏览器实例登录后生成一条记录，用于「最多同时在线设备数」限流：
超过上限时吊销最早登录的会话，并主动断开其 WebSocket。

设计要点：
- ``sid`` 写进 JWT，鉴权时与用户查询合并校验，被踢设备立即 401；
- ``device_id`` 是前端 localStorage 里的随机标识，同一浏览器重复登录
  复用同一行，不额外占用名额；
- 只做计数与吊销，不区分设备类型、不维护设备列表 UI。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        Index("ix_user_sessions_user_active", "user_id", "revoked_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sid: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=func.now(), server_default=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
