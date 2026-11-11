"""publisher_blacklists：发单人黑名单（平表）

Revision ID: 069_publisher_blacklist
Revises: 068_publish_permission
Create Date: 2026-11-11 12:00:00.000000

发单人维度的账号级黑名单：只影响「被拉黑用户接该发单人的订单」，
不涉及订单数据与已有报名记录。迁移为纯加法，回滚删表即可。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "069_publisher_blacklist"
down_revision: Union[str, None] = "068_publish_permission"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "publisher_blacklists",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("publisher_id", sa.Integer(), nullable=False),
        sa.Column("blocked_user_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["publisher_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_publisher_blacklists_publisher_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["blocked_user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_publisher_blacklists_blocked_user_id_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_publisher_blacklists"),
        sa.UniqueConstraint(
            "publisher_id",
            "blocked_user_id",
            name="uq_publisher_blacklists_publisher_blocked",
        ),
    )
    # 「当前查看者被谁拉黑」反查：blocked_user_id 在前，publisher_id 在后。
    # publisher_id 单独查询由上面的唯一约束（最左列）覆盖，无需重复建索引。
    op.create_index(
        "ix_publisher_blacklists_blocked_user_id_publisher_id",
        "publisher_blacklists",
        ["blocked_user_id", "publisher_id"],
    )


def downgrade() -> None:
    # drop_table 会一并删除索引与外键
    op.drop_table("publisher_blacklists")
