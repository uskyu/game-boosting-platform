"""
Order model module.
Defines the Order entity for game boosting service requests.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import JSON, Enum, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base
from app.models.user import UserRole

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


class ClaimStatus(str, PyEnum):
    OPEN = "OPEN"
    PAUSED = "PAUSED"
    FULL = "FULL"
    CLOSED = "CLOSED"


class ClaimLifecycleStatus(str, PyEnum):
    """Per-claim delivery lifecycle (名额制): each claim independently goes
    CLAIMED -> DELIVERED -> SETTLED without affecting the other claims."""

    CLAIMED = "CLAIMED"      # Booster registered on the order, work in progress
    DELIVERED = "DELIVERED"  # Booster submitted completion, awaiting review
    SETTLED = "SETTLED"      # Review approved and payout settled


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

    # Public order content
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    intro: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Pricing
    price_min: Mapped[Decimal | None] = mapped_column(Numeric(precision=10, scale=2), nullable=True)
    price_max: Mapped[Decimal | None] = mapped_column(Numeric(precision=10, scale=2), nullable=True)
    price: Mapped[Decimal] = mapped_column(
        Numeric(precision=10, scale=2),
        nullable=False,
    )

    # 用户发单附加信息
    # 老板联系 ID：仅发布人、管理员与已接单打手可见（序列化层控制）
    boss_contact: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 炸单赔偿金：打手接单时从其可用余额冻结，结算时返还/扣除
    compensation_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=10, scale=2), nullable=True
    )
    # 到账时效：天部分 0-30 + 小时部分 0-23（都为 null=不设置；交付后到时自动结算）
    payout_delay_days: Mapped[int | None] = mapped_column(nullable=True)
    payout_delay_hours: Mapped[int | None] = mapped_column(nullable=True)

    # Dispatch controls (legacy status remains the workflow status)
    max_claims: Mapped[int] = mapped_column(default=1, server_default="1", nullable=False)
    claimed_count: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)
    claim_status: Mapped[ClaimStatus] = mapped_column(Enum(ClaimStatus, name="claim_status_enum", values_callable=lambda x: [e.value for e in x]), default=ClaimStatus.OPEN, server_default="OPEN", nullable=False, index=True)
    deadline: Mapped[datetime | None] = mapped_column(nullable=True)
    is_archived: Mapped[bool] = mapped_column(default=False, server_default="0", nullable=False, index=True)
    attachments: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    delivery_attachments: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    # 打手结束订单时提交的汇报说明（区别于老板备注 notes）
    delivery_note: Mapped[str | None] = mapped_column(Text, nullable=True)

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
        return (self.status == OrderStatus.PENDING and self.claim_status == ClaimStatus.OPEN
                and not self.is_archived and self.claimed_count < self.max_claims)

    @property
    def is_deliverable(self) -> bool:
        """Check if booster can submit completion."""
        return self.status == OrderStatus.LOCKED and self.booster_id is not None

    @property
    def is_confirmable(self) -> bool:
        """Check if customer can confirm completion."""
        return self.status == OrderStatus.DELIVERED

    @property
    def escrow_amount(self) -> Decimal | None:
        """发布人托管总额：非管理员发布时 = price × max_claims，管理员发布（平台单）无托管。"""
        publisher = self.user
        if publisher is None or publisher.role == UserRole.ADMIN:
            return None
        return Decimal(str(self.price)) * int(self.max_claims)


class OrderClaim(Base):
    """A booster claim (名额), retained separately so multi-claim orders remain auditable.

    Each claim walks its own lifecycle CLAIMED -> DELIVERED -> SETTLED; the
    order itself only completes once every claim is settled and the quota is
    exhausted (or claiming was closed).
    """
    __tablename__ = "order_claims"
    __table_args__ = (UniqueConstraint("order_id", "booster_id", name="uq_order_claim_booster"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    booster_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[ClaimLifecycleStatus] = mapped_column(
        Enum(
            ClaimLifecycleStatus,
            name="claim_lifecycle_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=ClaimLifecycleStatus.CLAIMED,
        server_default="CLAIMED",
        nullable=False,
        index=True,
    )
    delivery_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_attachments: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=func.now(), server_default=func.now(), nullable=False)
