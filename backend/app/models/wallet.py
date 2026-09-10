"""
Wallet model module.
Defines the Wallet and WalletTransaction entities for booster balances.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.order import Order
    from app.models.user import User


class WalletTransactionType(str, PyEnum):
    """
    Enumeration of wallet transaction types.

    Amount sign convention (kept consistent across all records):
    - Money entering available balance is recorded as positive.
    - Money leaving available balance is recorded as negative.
    """

    ORDER_INCOME = "ORDER_INCOME"               # 订单结算入账 (+)
    ADMIN_ADJUST = "ADMIN_ADJUST"               # 管理员调账 (+/-)
    WITHDRAWAL_FREEZE = "WITHDRAWAL_FREEZE"     # 提现冻结，可用扣减 (-)
    WITHDRAWAL_REFUND = "WITHDRAWAL_REFUND"     # 提现驳回解冻，可用回补 (+)
    WITHDRAWAL_PAID = "WITHDRAWAL_PAID"         # 提现打款完成，冻结扣减 (-)
    # 用户发单托管（发布人侧）
    ESCROW_HOLD = "ESCROW_HOLD"                 # 发单托管冻结，可用扣减 (-)，冻结增加
    ESCROW_RELEASE = "ESCROW_RELEASE"           # 托管解冻退回，可用回补 (+)，冻结减少
    ORDER_PAYMENT = "ORDER_PAYMENT"             # 订单打款支出，冻结扣减 (-)
    # 炸单赔偿金（打手侧）
    DEPOSIT_HOLD = "DEPOSIT_HOLD"               # 接单冻结炸单赔偿金，可用扣减 (-)，冻结增加
    DEPOSIT_RELEASE = "DEPOSIT_RELEASE"         # 赔偿金解冻返还，可用回补 (+)，冻结减少
    COMPENSATION_DEDUCT = "COMPENSATION_DEDUCT"  # 炸单赔偿扣除，冻结扣减 (-)，不返还
    # 自助充值入账（易支付）。新增枚举值一律追加在末尾：
    # MySQL ENUM 只能靠 ALTER TABLE 修改，追加不会触发表数据重写。
    RECHARGE = "RECHARGE"                       # 易支付充值到账 (+)，不计入累计收入
    # 保证金（可用余额 ⇄ 保证金余额 划转，保证金不可消费、不可提现）
    DEPOSIT_TRANSFER_IN = "DEPOSIT_TRANSFER_IN"    # 余额转入保证金，可用扣减 (-)，保证金增加
    DEPOSIT_TRANSFER_OUT = "DEPOSIT_TRANSFER_OUT"  # 保证金转回余额，可用回补 (+)，保证金减少


class Wallet(Base):
    """
    Wallet model holding per-user balances.

    One row per user (user_id is unique). Balances are Decimal(12,2) and
    must only be mutated through WalletService which serializes updates
    with a SELECT ... FOR UPDATE row lock.
    """

    __tablename__ = "wallets"

    # Primary key
    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        index=True,
    )

    # Owner - one wallet per user
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Balances
    available_balance: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        default=Decimal("0.00"),
        server_default="0.00",
        nullable=False,
    )

    frozen_balance: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        default=Decimal("0.00"),
        server_default="0.00",
        nullable=False,
    )

    # 保证金余额：由可用余额转入，转入后冻结不可消费、不可提现，
    # 仅用于保证金档位权益（优先接单/免炸单赔付金/结账时效）与炸单扣款。
    # 与 frozen_balance 区分开，避免和提现冻结、发单托管混在一个池子里。
    deposit_balance: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        default=Decimal("0.00"),
        server_default="0.00",
        nullable=False,
    )

    total_income: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        default=Decimal("0.00"),
        server_default="0.00",
        nullable=False,
    )

    total_withdrawn: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        default=Decimal("0.00"),
        server_default="0.00",
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

    transactions: Mapped[list["WalletTransaction"]] = relationship(
        "WalletTransaction",
        back_populates="wallet",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<Wallet(id={self.id}, user_id={self.user_id}, "
            f"available={self.available_balance}, frozen={self.frozen_balance})>"
        )


class WalletTransaction(Base):
    """
    Wallet transaction ledger entry.

    Every balance mutation writes exactly one row. balance_before /
    balance_after always snapshot the wallet's available_balance around
    the mutation. The (order_id, booster_id, type) unique constraint makes
    order settlement idempotent per booster - MySQL unique indexes do not
    treat NULLs as duplicates, so rows without an order_id or booster_id
    are unaffected.
    """

    __tablename__ = "wallet_transactions"
    __table_args__ = (
        UniqueConstraint("order_id", "booster_id", "type", name="uq_wallet_tx_order_booster_type"),
        # 充值入账幂等：同一充值订单只允许一条流水（MySQL 唯一索引不把 NULL 视为重复）
        UniqueConstraint("recharge_order_id", name="uq_wallet_tx_recharge_order"),
        Index("ix_wallet_tx_wallet_created", "wallet_id", "created_at"),
    )
    # Primary key
    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        index=True,
    )

    wallet_id: Mapped[int] = mapped_column(
        ForeignKey("wallets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    type: Mapped[WalletTransactionType] = mapped_column(
        Enum(
            WalletTransactionType,
            name="wallet_transaction_type_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )

    # Signed amount: positive = money in, negative = money out
    amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        nullable=False,
    )

    balance_before: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        nullable=False,
    )

    balance_after: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        nullable=False,
    )

    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Which booster the settlement belongs to (multi-claim orders settle
    # each booster independently). NULL for non-order ledger entries.
    booster_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    withdrawal_id: Mapped[int | None] = mapped_column(
        ForeignKey("withdrawal_requests.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # 易支付充值入账来源；NULL 表示非充值流水。配合唯一键保证不重复入账。
    recharge_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("recharge_orders.id", ondelete="SET NULL"),
        nullable=True,
    )

    operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    remark: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    wallet: Mapped["Wallet"] = relationship(
        "Wallet",
        back_populates="transactions",
        foreign_keys=[wallet_id],
        lazy="joined",
    )

    order: Mapped["Order | None"] = relationship(
        "Order",
        foreign_keys=[order_id],
        lazy="joined",
    )

    def __repr__(self) -> str:
        return (
            f"<WalletTransaction(id={self.id}, wallet_id={self.wallet_id}, "
            f"type={self.type.value}, amount={self.amount})>"
        )
