"""User publishing escrow: boss contact, compensation deposit, payout delay.

- orders gains boss_contact / compensation_amount / payout_delay_days
- wallet_transaction_type_enum is extended (old values preserved) with:
  ESCROW_HOLD / ESCROW_RELEASE / ORDER_PAYMENT (publisher escrow flow) and
  DEPOSIT_HOLD / DEPOSIT_RELEASE / COMPENSATION_DEDUCT (booster deposit flow)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "022_user_publishing_escrow"
down_revision: Union[str, None] = "021_withdrawal_qrcode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 旧枚举值必须原样保留（MySQL MODIFY 会整体替换枚举定义）
_OLD_ENUM_VALUES = (
    "'ORDER_INCOME','ADMIN_ADJUST','WITHDRAWAL_FREEZE',"
    "'WITHDRAWAL_REFUND','WITHDRAWAL_PAID'"
)
_NEW_ENUM_VALUES = (
    _OLD_ENUM_VALUES
    + ",'ESCROW_HOLD','ESCROW_RELEASE','ORDER_PAYMENT'"
    + ",'DEPOSIT_HOLD','DEPOSIT_RELEASE','COMPENSATION_DEDUCT'"
)


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("boss_contact", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column(
            "compensation_amount", sa.Numeric(precision=10, scale=2), nullable=True
        ),
    )
    op.add_column(
        "orders",
        sa.Column("payout_delay_days", sa.Integer(), nullable=True),
    )
    # MySQL 枚举扩展：MODIFY 整体替换定义，旧值全部保留在新列表前部
    op.execute(
        f"ALTER TABLE wallet_transactions MODIFY COLUMN `type` "
        f"ENUM({_NEW_ENUM_VALUES}) NOT NULL"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE wallet_transactions MODIFY COLUMN `type` "
        f"ENUM({_OLD_ENUM_VALUES}) NOT NULL"
    )
    op.drop_column("orders", "payout_delay_days")
    op.drop_column("orders", "compensation_amount")
    op.drop_column("orders", "boss_contact")
