"""
Game model module.
Defines the game catalog entity and related enumerations.
"""

from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import JSON, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class GameCategory(str, PyEnum):
    """Supported game categories for the catalog."""

    MOBA = "MOBA"
    FPS = "FPS"
    RPG = "RPG"
    RACING = "RACING"
    CARD = "CARD"
    SPORTS = "SPORTS"
    STRATEGY = "STRATEGY"
    FIGHTING = "FIGHTING"
    SURVIVAL = "SURVIVAL"
    RHYTHM = "RHYTHM"


class GamePlatform(str, PyEnum):
    """Supported game platforms for the catalog."""

    MOBILE = "MOBILE"
    PC = "PC"
    BOTH = "BOTH"


class Game(TimestampMixin, Base):
    """Game catalog model."""

    __tablename__ = "games"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    english_name: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
    )

    category: Mapped[GameCategory] = mapped_column(
        Enum(
            GameCategory,
            name="game_category_enum",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        index=True,
    )

    platform: Mapped[GamePlatform] = mapped_column(
        Enum(
            GamePlatform,
            name="game_platform_enum",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        index=True,
    )

    icon_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    cover_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    color_theme: Mapped[str | None] = mapped_column(
        String(7),
        nullable=True,
    )

    service_template: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        default=True,
        nullable=False,
        index=True,
    )

    sort_order: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:
        return (
            f"<Game(id={self.id}, name={self.name!r}, category={self.category.value}, "
            f"platform={self.platform.value})>"
        )
