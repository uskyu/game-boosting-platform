"""售后扣划新增保证金抵扣流水类型：wallet_transaction_type_enum 追加一个类型。

老板 2026-10-18 拍板：售后扣款「先扣打手可用余额 → 不足部分从保证金补扣」，
两处合计仍不足则拒绝结案（不再静默按可用余额截断）。保证金那条腿单独记
``AFTERSALE_DEPOSIT_DEDUCTION``（只动 deposit_balance，可用余额不变），
沿用 ``AFTERSALE_DEDUCTION`` 记录可用余额那一条，两条流水各自对得上
``balance_before/balance_after`` 快照；同时靠 (order_id, booster_id, type)
唯一键保持「每单一类流水一条」的幂等口径。

追加在枚举末尾，MySQL ENUM 追加不重写表数据（与 029/032/044/054 的格式一致）。

Revision ID: 056_aftersale_deposit_tx_type
Revises: 055_after_sale_closed_status
Create Date: 2026-10-18 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "056_aftersale_deposit_tx_type"
down_revision: Union[str, None] = "055_after_sale_closed_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 追加在末尾，MySQL ENUM 追加不重写表
_OLD_ENUM_VALUES = (
    "'ORDER_INCOME','ADMIN_ADJUST','WITHDRAWAL_FREEZE',"
    "'WITHDRAWAL_REFUND','WITHDRAWAL_PAID',"
    "'ESCROW_HOLD','ESCROW_RELEASE','ORDER_PAYMENT',"
    "'DEPOSIT_HOLD','DEPOSIT_RELEASE','COMPENSATION_DEDUCT','RECHARGE',"
    "'DEPOSIT_TRANSFER_IN','DEPOSIT_TRANSFER_OUT',"
    "'CANCEL_COMPENSATION_IN','CANCEL_COMPENSATION_DEDUCT',"
    "'AFTERSALE_DEDUCTION','AFTERSALE_REFUND'"
)
_NEW_ENUM_VALUES = _OLD_ENUM_VALUES + ",'AFTERSALE_DEPOSIT_DEDUCTION'"


def upgrade() -> None:
    # 仅MySQL：MODIFY 整体替换枚举定义；本项目生产/CI 均为 MySQL，切库需重写本迁移
    op.execute(
        f"ALTER TABLE wallet_transactions MODIFY COLUMN `type` "
        f"ENUM({_NEW_ENUM_VALUES}) NOT NULL"
    )


def downgrade() -> None:
    # 缩短枚举前先把保证金抵扣流水改写为语义最接近的既有类型：
    # DEPOSIT_TRANSFER_OUT 同样「只减保证金、不动可用余额」。
    op.execute(
        "UPDATE wallet_transactions SET type = 'DEPOSIT_TRANSFER_OUT' "
        "WHERE type = 'AFTERSALE_DEPOSIT_DEDUCTION'"
    )
    op.execute(
        f"ALTER TABLE wallet_transactions MODIFY COLUMN `type` "
        f"ENUM({_OLD_ENUM_VALUES}) NOT NULL"
    )
