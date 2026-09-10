"""保证金：钱包保证金余额、划转流水类型、总开关与阶梯配置表。

- ``wallets.deposit_balance``：独立于 frozen_balance 的保证金余额
  （避免与提现冻结、发单托管混在同一池子）
- ``wallet_transaction_type_enum`` 追加 DEPOSIT_TRANSFER_IN / DEPOSIT_TRANSFER_OUT
- 新表 ``deposit_settings``：保证金模式总开关（单行，默认**关闭**）
- 新表 ``deposit_tiers``：后台可维护的保证金阶梯（门槛 / 等待秒数 /
  免炸单赔付金 / 结账时效），并写入老板给定的默认阶梯
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "032_deposit_tiers"
down_revision: Union[str, None] = "031_min_recharge_one_cent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 追加在末尾，MySQL ENUM 追加不重写表
_OLD_ENUM_VALUES = (
    "'ORDER_INCOME','ADMIN_ADJUST','WITHDRAWAL_FREEZE',"
    "'WITHDRAWAL_REFUND','WITHDRAWAL_PAID',"
    "'ESCROW_HOLD','ESCROW_RELEASE','ORDER_PAYMENT',"
    "'DEPOSIT_HOLD','DEPOSIT_RELEASE','COMPENSATION_DEDUCT','RECHARGE'"
)
_NEW_ENUM_VALUES = _OLD_ENUM_VALUES + ",'DEPOSIT_TRANSFER_IN','DEPOSIT_TRANSFER_OUT'"

# 默认阶梯：保证金 / 接单等待秒数 / 免炸单赔付金 / 结账时效(小时)
_DEFAULT_TIERS = [
    (0, 30, False, 72),
    (100, 30, True, 72),
    (300, 20, True, 72),
    (500, 10, True, 48),
    (1000, 0, True, 1),
]


def upgrade() -> None:
    op.add_column(
        "wallets",
        sa.Column(
            "deposit_balance",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
            server_default="0.00",
        ),
    )

    # 仅MySQL：MODIFY 整体替换枚举定义；本项目生产/CI 均为 MySQL，切库需重写本迁移
    op.execute(
        f"ALTER TABLE wallet_transactions MODIFY COLUMN `type` "
        f"ENUM({_NEW_ENUM_VALUES}) NOT NULL"
    )

    # 保证金模式总开关：默认关闭，部署后行为不变，需管理员在后台手动开启
    op.create_table(
        "deposit_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column(
            "return_cooldown_days", sa.Integer(), nullable=False, server_default="7"
        ),
        sa.Column(
            "default_compensation",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
            server_default="20.00",
        ),
        sa.Column(
            "settlement_mode",
            sa.String(20),
            nullable=False,
            server_default="AFTER_DELIVERY",
        ),
        sa.Column("alias", sa.String(50), nullable=True),
        sa.Column(
            "updated_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    # 保证金模式「通过后计时」需要在名额上记录审核通过时间
    op.add_column(
        "order_claims",
        sa.Column("approved_at", sa.DateTime(), nullable=True),
    )

    tiers = op.create_table(
        "deposit_tiers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("threshold", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("wait_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "exempt_compensation", sa.Boolean(), nullable=False, server_default="0"
        ),
        sa.Column("settle_hours", sa.Integer(), nullable=False, server_default="72"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("threshold", name="uq_deposit_tiers_threshold"),
    )

    op.bulk_insert(
        tiers,
        [
            {
                "threshold": threshold,
                "wait_seconds": wait_seconds,
                "exempt_compensation": exempt,
                "settle_hours": settle_hours,
                "enabled": True,
            }
            for threshold, wait_seconds, exempt, settle_hours in _DEFAULT_TIERS
        ],
    )


def downgrade() -> None:
    op.drop_column("order_claims", "approved_at")
    op.drop_table("deposit_tiers")
    op.drop_table("deposit_settings")

    # 缩短枚举前先把保证金划转流水改写为等价的 ADMIN_ADJUST，保留金额与余额快照
    op.execute(
        "UPDATE wallet_transactions SET type = 'ADMIN_ADJUST' "
        "WHERE type IN ('DEPOSIT_TRANSFER_IN', 'DEPOSIT_TRANSFER_OUT')"
    )
    # 仅MySQL：MODIFY 整体替换枚举定义
    op.execute(
        f"ALTER TABLE wallet_transactions MODIFY COLUMN `type` "
        f"ENUM({_OLD_ENUM_VALUES}) NOT NULL"
    )

    op.drop_column("wallets", "deposit_balance")
