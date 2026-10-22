"""wallet_transaction_type_enum 追加 SERVICE_FEE_SETTLE

Revision ID: 063_service_fee_tx_type
Revises: 062_platform_income
Create Date: 2026-10-22 19:15:00.000000

订单服务费要在流水上留痕：结单时从发布人托管冻结里扣整单价格，
打手拿净额，差额（服务费）既扣减冻结、又写入 platform_income。
ORDER_PAYMENT 的唯一键是 (order_id, booster_id, type)，同一名额不能
复用；因此追加独立类型，账务对账更清楚。

追加在枚举末尾，MySQL ENUM 追加不重写表数据。
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "063_service_fee_tx_type"
down_revision: Union[str, None] = "062_platform_income"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 062 时 wallet_transactions.type 的全部取值（含 058 追加的 RECHARGE_REVERSAL）
_OLD_ENUM_VALUES = (
    "'ORDER_INCOME','ADMIN_ADJUST','WITHDRAWAL_FREEZE',"
    "'WITHDRAWAL_REFUND','WITHDRAWAL_PAID',"
    "'ESCROW_HOLD','ESCROW_RELEASE','ORDER_PAYMENT',"
    "'DEPOSIT_HOLD','DEPOSIT_RELEASE','COMPENSATION_DEDUCT','RECHARGE',"
    "'DEPOSIT_TRANSFER_IN','DEPOSIT_TRANSFER_OUT',"
    "'CANCEL_COMPENSATION_IN','CANCEL_COMPENSATION_DEDUCT',"
    "'AFTERSALE_DEDUCTION','AFTERSALE_REFUND','AFTERSALE_DEPOSIT_DEDUCTION',"
    "'RECHARGE_REVERSAL'"
)
_NEW_ENUM_VALUES = _OLD_ENUM_VALUES + ",'SERVICE_FEE_SETTLE'"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE wallet_transactions MODIFY COLUMN `type` "
        f"ENUM({_NEW_ENUM_VALUES}) NOT NULL"
    )


def downgrade() -> None:
    # 缩短枚举前先改写为语义最接近的既有类型（同样只动冻结余额）。
    op.execute(
        "UPDATE wallet_transactions SET type = 'ORDER_PAYMENT' "
        "WHERE type = 'SERVICE_FEE_SETTLE'"
    )
    op.execute(
        f"ALTER TABLE wallet_transactions MODIFY COLUMN `type` "
        f"ENUM({_OLD_ENUM_VALUES}) NOT NULL"
    )
