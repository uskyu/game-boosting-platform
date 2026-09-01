"""
Withdrawal model module.
Defines the WithdrawalRequest entity for booster payout processing.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class WithdrawalChannel(str, PyEnum):
    """Payment channel the booster wants to receive money through."""

    ALIPAY = "ALIPAY"
    WECHAT = "WECHAT"
    BANK = "BANK"


class WithdrawalStatus(str, PyEnum):
    """
    Withdrawal lifecycle:

    PENDING -> APPROVED -> PAID
       |
       +-> REJECTED (frozen amount refunded to available balance)
    """

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PAID = "PAID"


class WithdrawalRequest(Base):
    """
    Withdrawal request submitted by a user and reviewed/paid by an admin.

    Creating the request freezes the amount (via WalletService); approving
    keeps it frozen, rejecting unfreezes it, and marking paid deducts it
    from frozen and accumulates total_withdrawn.
    """

    __tablename__ = "withdrawal_requests"
    __table_args__ = (
        Index("ix_withdrawal_user_created", "user_id", "created_at"),
        Index("ix_withdrawal_status_created", "status", "created_at"),
    )

    # Primary key
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

    amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        nullable=False,
    )

    channel: Mapped[WithdrawalChannel] = mapped_column(
        Enum(
            WithdrawalChannel,
            name="withdrawal_channel_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )

    account_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    account_no: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    status: Mapped[WithdrawalStatus] = mapped_column(
        Enum(
            WithdrawalStatus,
            name="withdrawal_status_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=WithdrawalStatus.PENDING,
        server_default="PENDING",
        nullable=False,
        index=True,
    )

    reject_reason: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    payment_reference: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    reviewed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
    )

    paid_by: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    paid_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
    )

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

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[user_id],
        lazy="joined",
    )

    def __repr__(self) -> str:
        return (
            f"<WithdrawalRequest(id={self.id}, user_id={self.user_id}, "
            f"amount={self.amount}, status={self.status.value})>"
        )
