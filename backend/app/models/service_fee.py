"""全局服务费设置（单行，id=1）。

全局费率应用于新发布的管理员订单，并在创建时固定到订单上。逐单设置开关
仅控制发布表单是否允许单独关闭服务费或自定义费率。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric
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
    # 是否允许发布订单时单独开关服务费或覆盖全局费率。默认关闭，继续统一使用全局费率。
    individual_service_fee_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
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
        return f"<ServiceFeeSetting rate={self.service_fee_rate} individual={self.individual_service_fee_enabled}>"
