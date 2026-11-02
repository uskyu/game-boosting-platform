"""提现手续费列

Revision ID: 064_withdrawal_fee
Revises: 063_service_fee_tx_type
Create Date: 2026-11-02 18:10:00.000000

后台新增可调控的提现费率。申请提现时把费率/手续费/实际到账额快照到
withdrawal_requests，之后修改费率不影响已提交的申请。

- amount 仍是申请金额（也是冻结金额）；
- net_amount 是渠道实际打款金额（申请金额 - 手续费）；
- 历史数据没有手续费，回填 net_amount = amount。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "064_withdrawal_fee"
down_revision: Union[str, None] = "063_service_fee_tx_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "withdrawal_rule_settings",
        sa.Column(
            "withdrawal_fee_rate",
            sa.Numeric(precision=5, scale=2),
            nullable=False,
            server_default="0",
            comment="提现手续费率（百分比）",
        ),
    )
    op.add_column(
        "withdrawal_requests",
        sa.Column(
            "fee_rate",
            sa.Numeric(precision=5, scale=2),
            nullable=False,
            server_default="0",
            comment="申请时的手续费率快照（百分比）",
        ),
    )
    op.add_column(
        "withdrawal_requests",
        sa.Column(
            "fee_amount",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
            server_default="0",
            comment="手续费金额",
        ),
    )
    op.add_column(
        "withdrawal_requests",
        sa.Column(
            "net_amount",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
            server_default="0",
            comment="实际打款金额（申请金额 - 手续费）",
        ),
    )
    # 历史提现没有手续费：实际打款额 = 申请金额，保持旧口径。
    op.execute(
        "UPDATE withdrawal_requests SET net_amount = amount "
        "WHERE net_amount = 0 AND amount > 0"
    )


def downgrade() -> None:
    op.drop_column("withdrawal_requests", "net_amount")
    op.drop_column("withdrawal_requests", "fee_amount")
    op.drop_column("withdrawal_requests", "fee_rate")
    op.drop_column("withdrawal_rule_settings", "withdrawal_fee_rate")
