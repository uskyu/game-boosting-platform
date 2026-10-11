"""订单争议前状态快照（撤销争议时据此恢复）。

orders 新增可空枚举列 pre_dispute_status：dispute_order 置 DISPUTED 前
记录原状态；管理员撤销争议（cancel_dispute）时订单回到该状态——若争议前
为 DELIVERED（或仍有 DELIVERED 名额），交付提交被重置、订单回 LOCKED 让
打手重新结单；否则直接恢复快照值（快照缺失时兜底 LOCKED）。

Revision ID: 048_order_pre_dispute_status
Revises: 047_claim_settlement_paused
Create Date: 2026-10-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "048_order_pre_dispute_status"
down_revision: Union[str, None] = "047_claim_settlement_paused"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 与 models/order.py OrderStatus 的值保持一致；复用 order_status_enum 名称，
# MySQL 每列独立持有 ENUM 定义，追加可空列不会影响既有 status 列。
_ORDER_STATUS_VALUES = (
    "PENDING", "LOCKED", "DELIVERED", "COMPLETED", "DISPUTED", "CANCELLED"
)


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "pre_dispute_status",
            sa.Enum(*_ORDER_STATUS_VALUES, name="order_status_enum"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("orders", "pre_dispute_status")
