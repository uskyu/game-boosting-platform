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
    # 取消订单赔偿（发单员对进行中订单发起取消时直扣打手保证金，无需对方同意）
    CANCEL_COMPENSATION_IN = "CANCEL_COMPENSATION_IN"        # 取消赔偿入账，发单员可用余额 (+)
    CANCEL_COMPENSATION_DEDUCT = "CANCEL_COMPENSATION_DEDUCT"  # 取消赔偿扣除，打手保证金扣减 (-)
    # 售后扣划（老板对已完成订单的售后通过：打手「可用余额 → 保证金」→ 老板可用余额）
    AFTERSALE_DEDUCTION = "AFTERSALE_DEDUCTION"  # 售后扣划，打手可用余额扣减 (-)
    AFTERSALE_REFUND = "AFTERSALE_REFUND"        # 售后收款，老板可用余额增加 (+)
    AFTERSALE_DEPOSIT_DEDUCTION = "AFTERSALE_DEPOSIT_DEDUCTION"
    # 售后扣划（保证金抵扣），打手保证金扣减 (-)，可用余额不变
    # 充值冲正（管理员把已到账充值改判失败）：可用余额扣回 (-)。
    # 冲正流水不带 recharge_order_id（那列的唯一键只给易支付回调入账用），
    # 商户单号写在 remark 里，靠类型 + 备注追溯。
    RECHARGE_REVERSAL = "RECHARGE_REVERSAL"
    # 订单服务费结转（发布人侧）：只扣冻结余额、不动可用余额，等额计入
    # platform_income。修复「发单按毛额冻结、结单只扣打手净额」导致服务费
    # 永久滞留在发布人冻结余额里的历史 bug。
    SERVICE_FEE_SETTLE = "SERVICE_FEE_SETTLE"


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
    # 仅用于保证金档位权益（优先接单/免炸单赔付金/结账时效）、炸单扣款
    # 与售后扣划的补足部分（可用余额不足时抵扣差额）。
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

    # 流水明细刻意用 lazy="raise"，不用 selectin。
    #
    # 原先是 lazy="selectin"：**每次**加载 Wallet 都会顺带把该钱包的全部流水
    # 查一遍（线上实测 SELECT Wallet 因此要 116ms，而等价的裸 SELECT 只要
    # 0.9ms；单次 get_or_create_wallet 实测 110ms）。而钱包是全站最热的表
    # 之一——接单、大厅、我的订单、钱包页、结算调度器全都要读它，这笔额外
    # 往返被放大了上百倍。项目里已经有两处代码专门改成「只选标量列、不加载
    # Wallet 实体」来绕开这个 selectin（order_service._booster_wallet_facts、
    # _cancel_compensation_collectible_batch），说明它早就是公认的负担。
    #
    # 现在全项目没有任何地方读取 wallet.transactions（要流水一律直接查
    # WalletTransaction 表，带分页和筛选）。改成 raise 有两个好处：
    #   1. 加载 Wallet 不再产生额外查询；
    #   2. 万一将来有人写出 N+1 式的 wallet.transactions 访问，会在开发期
    #      立刻抛错，而不是悄悄退化成线上慢查询。
    # 需要流水时请显式查询：
    #   select(WalletTransaction).where(WalletTransaction.wallet_id == wallet.id, ...)
    transactions: Mapped[list["WalletTransaction"]] = relationship(
        "WalletTransaction",
        back_populates="wallet",
        lazy="raise",
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
