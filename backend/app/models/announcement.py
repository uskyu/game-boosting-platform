"""Platform-wide HTML announcements and per-user display state."""

from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class AnnouncementFrequency(str, PyEnum):
    """How often an eligible user should see an announcement."""

    EVERY_OPEN = "EVERY_OPEN"
    DAILY = "DAILY"
    ONCE = "ONCE"


class Announcement(Base):
    """An administrator-managed announcement shown in the authenticated app shell."""

    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # Keep the source for future editing; only safe_content_html is public.
    content_html: Mapped[str] = mapped_column(Text, nullable=False)
    safe_content_html: Mapped[str] = mapped_column(Text, nullable=False)
    frequency: Mapped[AnnouncementFrequency] = mapped_column(
        Enum(
            AnnouncementFrequency,
            name="announcement_frequency_enum",
            values_callable=lambda values: [item.value for item in values],
        ),
        nullable=False,
        default=AnnouncementFrequency.DAILY,
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True, index=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True, index=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), default=func.now(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), default=func.now(), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    creator: Mapped["User | None"] = relationship("User", foreign_keys=[created_by], lazy="noload")


class AnnouncementView(Base):
    """Last display time for one announcement and one user."""

    __tablename__ = "announcement_views"
    __table_args__ = (
        UniqueConstraint("announcement_id", "user_id", name="uq_announcement_views_announcement_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    announcement_id: Mapped[int] = mapped_column(
        ForeignKey("announcements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    last_shown_at: Mapped[datetime] = mapped_column(
        DateTime(), default=func.now(), server_default=func.now(), nullable=False
    )
