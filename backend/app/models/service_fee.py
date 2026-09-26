"""全局服务费设置（单行，id=1）。

老板在后台设置一个全局服务费费率（百分数，如 8.00 表示 8%）：
- 管理员发布/编辑订单开启「服务费」但未手输费率时，发布瞬间把全局
  费率烙进 orders.service_fee_rate。之后调整全局费率只影响新发的
  订单，已发布订单的收费规则不变（钱的规则不能悄悄变）。
- 逐单手输费率优先于全局（某单要特例就特例）。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base


class ServiceFeeSetting(Base):
    """全局服务费设置（单行，id=1）。"""

    __tablename__ = "service_fee_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False, default=1)

    # 全局服务费费率（百分数，如 8.00 = 8%）。0 = 不收取。
    service_fee_rate: Mapped[Decimal] = mapped_column(
        Numeric(precision=5, scale=2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )

    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    updater: Mapped["User | None"] = relationship(
        "User", foreign_keys=[updated_by], lazy="noload"
    )

    def __repr__(self) -> str:
        return f"<ServiceFeeSetting rate={self.service_fee_rate}>"
