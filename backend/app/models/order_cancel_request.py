"""双方协商取消订单的按名额申请记录。"""

from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OrderCancelRequestStatus(str, PyEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"  # 订单/名额经其他途径关闭时结束待处理申请


class OrderCancelRequest(Base):
    """A cancellation proposal tied to exactly one booster claim/slot."""

    __tablename__ = "order_cancel_requests"
    __table_args__ = (
        Index("ix_cancel_requests_order_status", "order_id", "status"),
        Index("ix_cancel_requests_claim_status", "claim_id", "status"),
        Index("ix_cancel_requests_recipient_status", "recipient_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    claim_id: Mapped[int] = mapped_column(
        ForeignKey("order_claims.id", ondelete="CASCADE"), nullable=False
    )
    requester_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    recipient_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    requester_role: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    compensation_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )
    status: Mapped[OrderCancelRequestStatus] = mapped_column(
        Enum(
            OrderCancelRequestStatus,
            name="order_cancel_request_status_enum",
            values_callable=lambda values: [item.value for item in values],
        ),
        nullable=False,
        default=OrderCancelRequestStatus.PENDING,
        server_default="PENDING",
    )
    decision_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<OrderCancelRequest(id={self.id}, order_id={self.order_id}, "
            f"claim_id={self.claim_id}, status={self.status.value})>"
        )
