"""
Order model module.
Defines the Order entity for game boosting service requests.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import JSON, Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.booster_service import BoosterService
    from app.models.game import Game
    from app.models.user import User


class OrderStatus(str, PyEnum):
    """
    Enumeration of order statuses in the platform.
    Inherits from str for JSON serialization compatibility.
    """

    PENDING = "PENDING"         # Order created, waiting for booster assignment
    LOCKED = "LOCKED"           # Order assigned to a booster, in progress
    DELIVERED = "DELIVERED"     # Booster submitted completion, waiting for customer confirmation
    COMPLETED = "COMPLETED"     # Customer confirmed completion
    DISPUTED = "DISPUTED"       # Order has an issue requiring resolution
    CANCELLED = "CANCELLED"     # Order was cancelled


class PaymentStatus(str, PyEnum):
    """Payment status for orders."""

    UNPAID = "UNPAID"
    PAID = "PAID"
    REFUNDED = "REFUNDED"


class Order(Base):
    """
    Order model representing game boosting service requests.

    Workflow:
    1. Customer creates order (status: PENDING)
    2. Booster accepts order (status: LOCKED)
    3. Booster submits completion (status: DELIVERED)
    4. Customer confirms (status: COMPLETED) or auto-confirm after 72h
    5. If issues arise (status: DISPUTED)
    """

    __tablename__ = "orders"

    # Primary key
    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        index=True,
    )

    # Foreign keys
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    booster_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    game_id: Mapped[int | None] = mapped_column(
        ForeignKey("games.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    service_id: Mapped[int | None] = mapped_column(
        ForeignKey("booster_services.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Game information
    game_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    current_rank: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    target_rank: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    ai_tags: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    service_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )

    server: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )

    # Pricing
    price: Mapped[Decimal] = mapped_column(
        Numeric(precision=10, scale=2),
        nullable=False,
    )

    # Order status
    status: Mapped[OrderStatus] = mapped_column(
        Enum(
            OrderStatus,
            name="order_status_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=OrderStatus.PENDING,
        nullable=False,
        index=True,
    )

    # Detailed description provided by customer
    description_raw: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # AI-generated structured description
    description_ai: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Game account credentials (encrypted in production)
    game_account: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    game_password: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    locked_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
    )

    delivered_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
    )

    payment_status: Mapped[PaymentStatus] = mapped_column(
        Enum(
            PaymentStatus,
            name="payment_status_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=PaymentStatus.UNPAID,
        server_default="UNPAID",
        nullable=False,
        index=True,
    )

    paid_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
    )

    # Additional metadata
    priority: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="orders_as_customer",
        foreign_keys=[user_id],
        lazy="joined",
    )

    booster: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="orders_as_booster",
        foreign_keys=[booster_id],
        lazy="joined",
    )

    game: Mapped[Optional["Game"]] = relationship(
        "Game",
        lazy="joined",
    )

    booster_service: Mapped[Optional["BoosterService"]] = relationship(
        "BoosterService",
        lazy="joined",
    )

    def __repr__(self) -> str:
        return (
            f"<Order(id={self.id}, game={self.game_name!r}, "
            f"status={self.status.value}, price={self.price})>"
        )

    @property
    def is_assignable(self) -> bool:
        """Check if order can be assigned to a booster."""
        return self.status == OrderStatus.PENDING and self.booster_id is None

    @property
    def is_deliverable(self) -> bool:
        """Check if booster can submit completion."""
        return self.status == OrderStatus.LOCKED and self.booster_id is not None

    @property
    def is_confirmable(self) -> bool:
        """Check if customer can confirm completion."""
        return self.status == OrderStatus.DELIVERED
