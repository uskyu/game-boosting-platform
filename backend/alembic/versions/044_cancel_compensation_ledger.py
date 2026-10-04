"""取消订单赔偿账本：wallet_transaction_type_enum 追加两个类型。

发单员对进行中订单「申请取消」时立即生效：从接单人保证金直扣约定金额
（CANCEL_COMPENSATION_DEDUCT，只动 deposit_balance），等额补偿入账发单员
可用余额（CANCEL_COMPENSATION_IN）。两者均为本批新增语义，追加在枚举末尾，
MySQL ENUM 追加不重写表数据。

Revision ID: 044_cancel_compensation_ledger
Revises: 043_hall_recent_claims_toggle
Create Date: 2026-10-05 09:05:00.000000
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "044_cancel_compensation_ledger"
down_revision: Union[str, None] = "043_hall_recent_claims_toggle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 追加在末尾，MySQL ENUM 追加不重写表（与 029/032 的格式保持一致）
_OLD_ENUM_VALUES = (
    "'ORDER_INCOME','ADMIN_ADJUST','WITHDRAWAL_FREEZE',"
    "'WITHDRAWAL_REFUND','WITHDRAWAL_PAID',"
    "'ESCROW_HOLD','ESCROW_RELEASE','ORDER_PAYMENT',"
    "'DEPOSIT_HOLD','DEPOSIT_RELEASE','COMPENSATION_DEDUCT','RECHARGE',"
    "'DEPOSIT_TRANSFER_IN','DEPOSIT_TRANSFER_OUT'"
)
_NEW_ENUM_VALUES = _OLD_ENUM_VALUES + ",'CANCEL_COMPENSATION_IN','CANCEL_COMPENSATION_DEDUCT'"


def upgrade() -> None:
    # 仅MySQL：MODIFY 整体替换枚举定义；本项目生产/CI 均为 MySQL，切库需重写本迁移
    op.execute(
        f"ALTER TABLE wallet_transactions MODIFY COLUMN `type` "
        f"ENUM({_NEW_ENUM_VALUES}) NOT NULL"
    )


def downgrade() -> None:
    # 缩短枚举前先把两种取消赔偿流水改写为等价的既有类型，保留账务记录：
    # - CANCEL_COMPENSATION_IN（可用+）→ ADMIN_ADJUST（同为可用余额调整）
    # - CANCEL_COMPENSATION_DEDUCT（保证金-）→ DEPOSIT_TRANSFER_OUT（同为保证金出账）
    op.execute(
        "UPDATE wallet_transactions SET type = 'ADMIN_ADJUST' "
        "WHERE type = 'CANCEL_COMPENSATION_IN'"
    )
    op.execute(
        "UPDATE wallet_transactions SET type = 'DEPOSIT_TRANSFER_OUT' "
        "WHERE type = 'CANCEL_COMPENSATION_DEDUCT'"
    )
    op.execute(
        f"ALTER TABLE wallet_transactions MODIFY COLUMN `type` "
        f"ENUM({_OLD_ENUM_VALUES}) NOT NULL"
    )
