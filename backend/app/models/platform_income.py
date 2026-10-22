"""平台收入流水（仅后台可见）。

目前只记录订单服务费：结单时从发单人托管里扣整单价格，打手拿净额，
差额写入本表。它不是任何一个用户的钱包余额，所以单独建账，后台可查，
前台不暴露。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class PlatformIncome(Base):
    __tablename__ = "platform_income"
    __table_args__ = (
        # 幂等键：同一订单的同一打手名额只记一次服务费，重试不会重复入账。
        UniqueConstraint("order_id", "booster_id", "source", name="uq_platform_income_order_booster_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    booster_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    service_fee_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="SERVICE_FEE", server_default="SERVICE_FEE"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=func.now(), server_default=func.now()
    )
