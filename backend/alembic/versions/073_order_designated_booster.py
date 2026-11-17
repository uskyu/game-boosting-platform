"""orders 增加指定派单目标用户 designated_booster_id

Revision ID: 073_order_designated_booster
Revises: 072_chat_counterpart_contract
Create Date: 2026-11-17 12:00:00.000000

指定派单（v1）：订单发布时可点名一个接单人。纯加法迁移，无回填：
历史订单一律 NULL（大厅语义与旧行为完全一致）。

表级 CHECK 约束把「指定人」与「单人名额」绑定：指定单不允许 max_claims > 1，
应用层校验之外再由数据库兜底。

外键动作使用 NO ACTION 而不是 SET NULL：MySQL 8.0 不允许 CHECK 约束引用
带 ON DELETE SET NULL 的外键列（错误 3823，实测 8.0.43 必现）。两者在
本项目中二选一，这里保留表级 CHECK；应用层不做用户物理删除（停用走
is_active），因此不会出现因删除用户而需要 SET NULL 的场景。

MySQL 不允许删除被外键引用的索引（错误 1553），downgrade 必须先删 CHECK、
再删外键，最后才能删索引与列。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "073_order_designated_booster"
down_revision: Union[str, None] = "072_chat_counterpart_contract"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "ix_orders_designated_booster_id"
_FK = "fk_orders_designated_booster_id_users"
_CHECK = "ck_orders_designated_single_slot"


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("designated_booster_id", sa.Integer(), nullable=True),
    )
    op.create_index(_INDEX, "orders", ["designated_booster_id"], unique=False)
    op.create_foreign_key(
        _FK,
        "orders",
        "users",
        ["designated_booster_id"],
        ["id"],
        # 与 CHECK 约束互斥（MySQL 3823）：保留 CHECK，外键用 NO ACTION。
        ondelete="NO ACTION",
    )
    op.create_check_constraint(
        _CHECK,
        "orders",
        "designated_booster_id IS NULL OR max_claims = 1",
    )


def downgrade() -> None:
    # 顺序不能反：CHECK -> FK -> 索引 -> 列。MySQL 下被外键引用的索引
    # 无法直接删除（1553），必须先移除外键。
    op.drop_constraint(_CHECK, "orders", type_="check")
    op.drop_constraint(_FK, "orders", type_="foreignkey")
    op.drop_index(_INDEX, table_name="orders")
    op.drop_column("orders", "designated_booster_id")
