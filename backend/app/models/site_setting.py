"""Platform-wide site settings (a single logical row)."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class SiteSetting(Base):
    """The platform's public branding and description settings."""

    __tablename__ = "site_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False, default=1)
    site_name: Mapped[str] = mapped_column(String(200), nullable=False, default="游戏服务平台")
    site_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    site_logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    favicon_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), default=func.now(), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    updater: Mapped["User | None"] = relationship("User", foreign_keys=[updated_by], lazy="noload")
