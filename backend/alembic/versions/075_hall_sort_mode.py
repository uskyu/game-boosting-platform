"""site_settings 增加 hall_sort_mode：大厅公共列表排序模式

Revision ID: 075_hall_sort_mode
Revises: 074_order_cancelled_at
Create Date: 2026-11-22 00:00:00.000000

管理员可在后台选择大厅排序：NEWEST（发布时间倒序，原有行为）或
OLDEST（发布时间正序）。纯加法迁移，不回填历史行：server_default
'NEWEST' 等价于原行为，升级后的站点表现与升级前完全一致。

该设置只影响公开大厅列表；「我的派单」、后台派单管理与综合搜索结果
的排序保持不变。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "075_hall_sort_mode"
down_revision: str | None = "074_order_cancelled_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "site_settings",
        sa.Column(
            "hall_sort_mode",
            sa.String(length=16),
            nullable=False,
            server_default="NEWEST",
            comment="大厅公共列表排序：NEWEST=发布时间倒序，OLDEST=发布时间正序",
        ),
    )


def downgrade() -> None:
    op.drop_column("site_settings", "hall_sort_mode")
