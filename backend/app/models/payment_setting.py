"""Platform-wide 易支付（Epay）payment gateway settings (a single logical row).

商户密钥只保存在服务端，任何面向普通用户的接口都不会下发。
"""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User

# 默认支付方式（与易支付 type 取值一致）；管理员可在后台用 JSON 覆盖
DEFAULT_PAY_METHODS_JSON = (
    '[{"name": "支付宝", "type": "alipay"}, {"name": "微信", "type": "wxpay"}]'
)


class PaymentSetting(Base):
    """易支付网关配置：接口地址、商户 ID、商户密钥与支付方式。"""

    __tablename__ = "payment_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False, default=1)

    # 总开关。默认开启：管理员填好三项凭据保存即可收款，无需再手动打开。
    enabled: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=True, server_default="1"
    )

    # 易支付接口地址，例如 https://pay.example.com
    pay_address: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # 易支付商户 ID（pid）
    epay_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 易支付商户密钥。仅服务端使用，任何用户侧接口都不下发。
    epay_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # 异步回调基础地址（易支付服务器需要能访问到）。为空时回退到当前请求来源。
    notify_base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # 支付方式 JSON 数组文本：[{"name": "支付宝", "type": "alipay"}]
    pay_methods: Mapped[str | None] = mapped_column(Text(), nullable=True)

    # 单笔最低充值金额（元）
    min_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        nullable=False,
        default=Decimal("1.00"),
        server_default="1.00",
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

    @property
    def is_configured(self) -> bool:
        """三个必填项齐备且已启用，才允许用户充值。"""
        return bool(
            self.enabled
            and self.pay_address
            and self.epay_id
            and self.epay_key
        )

    def __repr__(self) -> str:
        return (
            f"<PaymentSetting(id={self.id}, enabled={self.enabled}, "
            f"pay_address={self.pay_address})>"
        )
