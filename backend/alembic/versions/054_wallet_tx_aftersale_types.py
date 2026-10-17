"""售后扣划流水类型：wallet_transaction_type_enum 追加两个类型。

售后申请通过后按约定金额从打手可用余额扣划并等额划给老板：打手侧记
AFTERSALE_DEDUCTION（可用余额 -），老板侧记 AFTERSALE_REFUND（可用余额 +）。
两者均为本批新增语义，追加在枚举末尾，MySQL ENUM 追加不重写表数据
（与 029/032/044 的格式保持一致）。

Revision ID: 054_wallet_tx_aftersale_types
Revises: 053_deposit_tier_daily_limit
Create Date: 2026-10-16 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "054_wallet_tx_aftersale_types"
down_revision: Union[str, None] = "053_deposit_tier_daily_limit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 追加在末尾，MySQL ENUM 追加不重写表
_OLD_ENUM_VALUES = (
    "'ORDER_INCOME','ADMIN_ADJUST','WITHDRAWAL_FREEZE',"
    "'WITHDRAWAL_REFUND','WITHDRAWAL_PAID',"
    "'ESCROW_HOLD','ESCROW_RELEASE','ORDER_PAYMENT',"
    "'DEPOSIT_HOLD','DEPOSIT_RELEASE','COMPENSATION_DEDUCT','RECHARGE',"
    "'DEPOSIT_TRANSFER_IN','DEPOSIT_TRANSFER_OUT',"
    "'CANCEL_COMPENSATION_IN','CANCEL_COMPENSATION_DEDUCT'"
)
_NEW_ENUM_VALUES = _OLD_ENUM_VALUES + ",'AFTERSALE_DEDUCTION','AFTERSALE_REFUND'"


def upgrade() -> None:
    # 仅MySQL：MODIFY 整体替换枚举定义；本项目生产/CI 均为 MySQL，切库需重写本迁移
    op.execute(
        f"ALTER TABLE wallet_transactions MODIFY COLUMN `type` "
        f"ENUM({_NEW_ENUM_VALUES}) NOT NULL"
    )


def downgrade() -> None:
    # 缩短枚举前先把两种售后流水改写为等价的既有类型，保留账务记录：
    # 两者都只动可用余额，与 ADMIN_ADJUST（可用余额调整）同义。
    op.execute(
        "UPDATE wallet_transactions SET type = 'ADMIN_ADJUST' "
        "WHERE type IN ('AFTERSALE_DEDUCTION', 'AFTERSALE_REFUND')"
    )
    op.execute(
        f"ALTER TABLE wallet_transactions MODIFY COLUMN `type` "
        f"ENUM({_OLD_ENUM_VALUES}) NOT NULL"
    )
