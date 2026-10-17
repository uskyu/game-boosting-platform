"""售后状态枚举追加 CLOSED：平台未做裁定的关结态。

管理员审阅新增 close 动作（必填原因、不动资金），老板也可对待处理售后
主动撤回；两者都落 CLOSED，与 APPROVED/REJECTED 区分「未裁定」口径。
值序必须与 app.models.order_after_sale.AfterSaleStatus 保持一致（CLOSED
在末尾），MySQL ENUM 追加不重写表数据（与 054 的格式保持一致）。

Revision ID: 055_after_sale_closed_status
Revises: 054_wallet_tx_aftersale_types
Create Date: 2026-10-16 11:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "055_after_sale_closed_status"
down_revision: Union[str, None] = "054_wallet_tx_aftersale_types"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 追加在末尾，MySQL ENUM 追加不重写表
_OLD_ENUM_VALUES = "'PENDING','APPROVED','REJECTED'"
_NEW_ENUM_VALUES = _OLD_ENUM_VALUES + ",'CLOSED'"


def upgrade() -> None:
    # 仅MySQL：MODIFY 整体替换枚举定义；本项目生产/CI 均为 MySQL，切库需重写本迁移
    op.execute(
        f"ALTER TABLE order_after_sales MODIFY COLUMN status "
        f"ENUM({_NEW_ENUM_VALUES}) NOT NULL"
    )


def downgrade() -> None:
    # 缩短枚举前先把 CLOSED 行回映为 REJECTED（同为不动资金的关结态），
    # 再缩短 ENUM（仿 054 的数据回映）。
    op.execute(
        "UPDATE order_after_sales SET status = 'REJECTED' "
        "WHERE status = 'CLOSED'"
    )
    op.execute(
        f"ALTER TABLE order_after_sales MODIFY COLUMN status "
        f"ENUM({_OLD_ENUM_VALUES}) NOT NULL"
    )
