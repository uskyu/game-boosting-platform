"""Platform-wide 易支付（Epay）payment gateway settings (a single logical row).

商户密钥只保存在服务端，任何面向普通用户的接口都不会下发。
回调地址不再由管理员填写：由充值请求的来源（Origin / 反向代理头）自动推导。
"""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User

# 支持的易支付方式：type 为上游参数，name 为界面文案
PAY_METHOD_NAMES: dict[str, str] = {
    "alipay": "支付宝",
    "wxpay": "微信",
}


class PaymentSetting(Base):
    """易支付网关配置：接口地址、商户 ID、商户密钥与启用的支付方式。"""

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

    # 启用的支付方式（勾选即启用）
    alipay_enabled: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=True, server_default="1"
    )
    wxpay_enabled: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=True, server_default="1"
    )

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
    def enabled_method_types(self) -> list[str]:
        """已勾选的支付方式 type，顺序固定为支付宝、微信。"""
        types: list[str] = []
        if self.alipay_enabled:
            types.append("alipay")
        if self.wxpay_enabled:
            types.append("wxpay")
        return types

    @property
    def is_configured(self) -> bool:
        """三项凭据齐备、已启用且至少勾选一种支付方式，才允许用户充值。"""
        return bool(
            self.enabled
            and self.pay_address
            and self.epay_id
            and self.epay_key
            and self.enabled_method_types
        )

    def __repr__(self) -> str:
        return (
            f"<PaymentSetting(id={self.id}, enabled={self.enabled}, "
            f"pay_address={self.pay_address}, methods={self.enabled_method_types})>"
        )
