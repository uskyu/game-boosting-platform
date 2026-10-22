"""user_sessions: 登录设备会话表（同时在线设备上限）

Revision ID: 061_user_sessions
Revises: 060_security_settings
Create Date: 2026-10-22 19:05:00.000000

每个浏览器实例登录生成一行，``sid`` 写进 JWT。超过
``site_settings.max_online_devices`` 时吊销最早登录的会话，并把对应
WebSocket 以 4002 关闭。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "061_user_sessions"
down_revision: Union[str, None] = "060_security_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("sid", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_reason", sa.String(length=32), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_sid", "user_sessions", ["sid"], unique=True)
    op.create_index("ix_user_sessions_device_id", "user_sessions", ["device_id"])
    op.create_index(
        "ix_user_sessions_user_active", "user_sessions", ["user_id", "revoked_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_user_sessions_user_active", table_name="user_sessions")
    op.drop_index("ix_user_sessions_device_id", table_name="user_sessions")
    op.drop_index("ix_user_sessions_sid", table_name="user_sessions")
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")
