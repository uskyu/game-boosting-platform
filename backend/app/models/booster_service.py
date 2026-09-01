"""
Booster service model module.
Defines booster-published service cards for the marketplace.
"""

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import JSON, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.game import Game
    from app.models.user import User


class BoosterService(TimestampMixin, Base):
    """Booster service card model."""

    __tablename__ = "booster_services"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        index=True,
    )

    booster_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    game_id: Mapped[int] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
        index=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    service_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    price_per_hour: Mapped[Decimal] = mapped_column(
        Numeric(precision=10, scale=2),
        nullable=False,
    )

    tags: Mapped[list[str] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    is_available: Mapped[bool] = mapped_column(
        default=True,
        nullable=False,
        index=True,
    )

    order_count: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
    )

    booster: Mapped["User"] = relationship(
        "User",
        foreign_keys=[booster_id],
        lazy="joined",
    )

    game: Mapped["Game"] = relationship(
        "Game",
        lazy="joined",
    )

    def __repr__(self) -> str:
        return (
            f"<BoosterService(id={self.id}, booster_id={self.booster_id}, "
            f"game_id={self.game_id}, service_type={self.service_type!r})>"
        )
