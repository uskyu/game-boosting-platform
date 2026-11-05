"""支付网关配置。

每家支付服务商一行，管理员在后台自行增删改，并选择「当前启用」的一家。
下单时按当前启用的网关取地址、商户号与密钥；历史订单记录当时的网关，
回调据此选择密钥，因此切换网关不会影响已创建的订单。

商户密钥只保存在服务端，任何面向普通用户的接口都不会下发。
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.recharge import RechargeOrder


class PaymentGateway(Base):
    """一条支付网关配置。

    ``submit_url`` 由管理员填写：勾选 ``append_submit_php`` 时按「基地址」
    理解，程序补 ``/submit.php``；不勾选时按「完整提交地址」原样使用。
    """

    __tablename__ = "payment_gateways"
    __table_args__ = (
        Index("ix_payment_gateway_active", "is_active"),
        Index("ix_payment_gateway_enabled", "enabled"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, index=True)

    # 显示名，例如「马支付」「立收通」
    name: Mapped[str] = mapped_column(String(50), nullable=False)

    # 管理员填写的提交地址：可能是基地址，也可能是完整入口
    submit_url: Mapped[str] = mapped_column(String(500), nullable=False)

    # True：地址按基地址处理，自动补 /submit.php；False：地址即完整入口
    append_submit_php: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=True, server_default="1"
    )

    # 易支付商户号（pid）
    merchant_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # 商户密钥。仅服务端使用，任何用户侧接口都不下发。
    secret_key: Mapped[str] = mapped_column(String(255), nullable=False)

    # 订单查询地址；留空时：需要补 .php 的网关默认推导 {基地址}/api.php，
    # 不需要的网关视为不支持查询（不臆造路径）。
    query_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    alipay_enabled: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=True, server_default="1"
    )
    wxpay_enabled: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=True, server_default="1"
    )

    # 停用后不可被选中，也不参与下单；历史订单仍可回调验签
    enabled: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=True, server_default="1"
    )

    # 当前启用：全表最多一行为 True，由服务层在事务内保证
    is_active: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=False, server_default="0"
    )

    created_at: Mapped[datetime] = mapped_column(
        default=func.now(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    recharge_orders: Mapped[list["RechargeOrder"]] = relationship(
        "RechargeOrder",
        back_populates="gateway",
        foreign_keys="RechargeOrder.gateway_id",
        lazy="noload",
    )

    @property
    def enabled_method_types(self) -> list[str]:
        """已勾选的支付方式，顺序固定为支付宝、微信。"""
        types: list[str] = []
        if self.alipay_enabled:
            types.append("alipay")
        if self.wxpay_enabled:
            types.append("wxpay")
        return types

    @property
    def is_configured(self) -> bool:
        """提交地址、商户号、密钥齐备且至少勾选一种支付方式。"""
        return bool(
            (self.submit_url or "").strip()
            and (self.merchant_id or "").strip()
            and (self.secret_key or "").strip()
            and self.enabled_method_types
        )

    def __repr__(self) -> str:
        return (
            f"<PaymentGateway(id={self.id}, name={self.name!r}, "
            f"active={self.is_active}, enabled={self.enabled})>"
        )
