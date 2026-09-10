"""Add 易支付 self-service recharge: payment settings, recharge orders and ledger link.

- 新表 ``payment_settings``：单行易支付网关配置（接口地址 / 商户ID / 商户密钥）
- 新表 ``recharge_orders``：用户充值订单
- ``wallet_transaction_type_enum`` 追加 ``RECHARGE``（追加在末尾，不触发表数据重写）
- ``wallet_transactions.recharge_order_id``：充值入账来源 + 唯一键保证不重复入账

充值金额进入钱包可用余额，发单托管与提现仍照旧从余额扣减。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "029_epay_recharge"
down_revision: Union[str, None] = "028_claim_cancelled_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# wallet_transaction_type_enum：新增枚举值一律追加在末尾，MySQL ENUM 追加不重写表
# （格式与 022_user_publishing_escrow 保持一致：MySQL MODIFY 整体替换枚举定义）
_OLD_ENUM_VALUES = (
    "'ORDER_INCOME','ADMIN_ADJUST','WITHDRAWAL_FREEZE',"
    "'WITHDRAWAL_REFUND','WITHDRAWAL_PAID',"
    "'ESCROW_HOLD','ESCROW_RELEASE','ORDER_PAYMENT',"
    "'DEPOSIT_HOLD','DEPOSIT_RELEASE','COMPENSATION_DEDUCT'"
)
_NEW_ENUM_VALUES = _OLD_ENUM_VALUES + ",'RECHARGE'"


def upgrade() -> None:
    op.create_table(
        "payment_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("pay_address", sa.String(500), nullable=True),
        sa.Column("epay_id", sa.String(64), nullable=True),
        sa.Column("epay_key", sa.String(255), nullable=True),
        sa.Column("notify_base_url", sa.String(500), nullable=True),
        sa.Column("pay_methods", sa.Text(), nullable=True),
        sa.Column(
            "min_amount",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
            server_default="1.00",
        ),
        sa.Column(
            "updated_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "recharge_orders",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("trade_no", sa.String(64), nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("payment_method", sa.String(32), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "SUCCESS",
                "CLOSED",
                name="recharge_status_enum",
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("epay_trade_no", sa.String(64), nullable=True),
        sa.Column("notify_payload", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
    )
    # trade_no 唯一索引，与模型 unique=True + index=True 的生成结果一致
    op.create_index(
        "ix_recharge_orders_trade_no", "recharge_orders", ["trade_no"], unique=True
    )
    op.create_index("ix_recharge_orders_user_id", "recharge_orders", ["user_id"])
    op.create_index("ix_recharge_orders_status", "recharge_orders", ["status"])
    op.create_index(
        "ix_recharge_user_created", "recharge_orders", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_recharge_status_created", "recharge_orders", ["status", "created_at"]
    )

    # 钱包流水：充值入账来源列 + 幂等唯一键
    op.add_column(
        "wallet_transactions",
        sa.Column("recharge_order_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_wallet_tx_recharge_order",
        "wallet_transactions",
        "recharge_orders",
        ["recharge_order_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_wallet_tx_recharge_order",
        "wallet_transactions",
        ["recharge_order_id"],
    )

    # 枚举追加 RECHARGE
    # 仅MySQL：MODIFY 整体替换枚举定义；本项目生产/CI 均为 MySQL，切库需重写本迁移
    op.execute(
        f"ALTER TABLE wallet_transactions MODIFY COLUMN `type` "
        f"ENUM({_NEW_ENUM_VALUES}) NOT NULL"
    )


def downgrade() -> None:
    # 缩短枚举前先把 RECHARGE 流水改写为等价的 ADMIN_ADJUST（同为可用余额增加），
    # 保留金额与余额快照，不删除任何账务记录。
    op.execute(
        "UPDATE wallet_transactions SET type = 'ADMIN_ADJUST' WHERE type = 'RECHARGE'"
    )
    # 仅MySQL：MODIFY 整体替换枚举定义
    op.execute(
        f"ALTER TABLE wallet_transactions MODIFY COLUMN `type` "
        f"ENUM({_OLD_ENUM_VALUES}) NOT NULL"
    )

    # MySQL 中外键依赖 uq_wallet_tx_recharge_order 这个索引，
    # 必须先删外键再删唯一键，否则报 1553「needed in a foreign key constraint」。
    op.drop_constraint(
        "fk_wallet_tx_recharge_order", "wallet_transactions", type_="foreignkey"
    )
    op.drop_constraint(
        "uq_wallet_tx_recharge_order", "wallet_transactions", type_="unique"
    )
    op.drop_column("wallet_transactions", "recharge_order_id")

    # recharge_orders 的索引同时被它自己的外键占用，逐个 DROP INDEX 会报 1553；
    # 直接 DROP TABLE 会连同索引与外键一起移除。
    op.drop_table("recharge_orders")

    op.drop_table("payment_settings")
