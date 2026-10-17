"""老板对已完成订单发起的售后申请记录。"""

from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AfterSaleStatus(str, PyEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"


class OrderAfterSale(Base):
    """An after-sale claim raised by the order publisher on a COMPLETED order.

    售后单 PENDING 期间该单打手禁止提现；管理员审阅通过/驳回，通过时的
    扣款划转由 after_sale_service.settle_approved_funds 承接。CLOSED 为
    平台未做裁定的关结态：管理员关闭或老板主动撤回，均不动资金。
    """

    __tablename__ = "order_after_sales"
    __table_args__ = (
        Index("ix_after_sales_order_status", "order_id", "status"),
        Index("ix_after_sales_booster_status", "booster_id", "status"),
        Index("ix_after_sales_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    requester_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    booster_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    # 与 OrderClaim.delivery_attachments 同型：JSON 列存 [{url,name,size,content_type}]
    images: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )
    status: Mapped[AfterSaleStatus] = mapped_column(
        Enum(
            AfterSaleStatus,
            name="after_sale_status_enum",
            values_callable=lambda values: [item.value for item in values],
        ),
        nullable=False,
        default=AfterSaleStatus.PENDING,
        server_default="PENDING",
    )
    decision_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<OrderAfterSale(id={self.id}, order_id={self.order_id}, "
            f"booster_id={self.booster_id}, status={self.status.value})>"
        )
