"""Add notifications and user_preferences tables.

Revision ID: 011_add_notifications_and_preferences
Revises: 010_add_reputation_fields
Create Date: 2026-04-09
"""

import sqlalchemy as sa
from alembic import op

revision = "011_add_notif_and_prefs"
down_revision = "010_add_reputation_fields"
branch_labels = None
depends_on = None

notification_type_enum = sa.Enum(
    "ORDER_ACCEPTED",
    "ORDER_DELIVERED",
    "ORDER_CONFIRMED",
    "ORDER_DISPUTED",
    "ORDER_CANCELLED",
    "NEW_MESSAGE",
    "APPLICATION_APPROVED",
    "APPLICATION_REJECTED",
    "REVIEW_RECEIVED",
    "SYSTEM_ANNOUNCEMENT",
    name="notification_type_enum",
)


def upgrade() -> None:
    notification_type_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("type", notification_type_enum, nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("link", sa.String(500), nullable=True),
        sa.Column("ref_id", sa.Integer(), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE",
            name="fk_notifications_user_id_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
    )
    op.create_index("ix_notifications_id", "notifications", ["id"])
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_type", "notifications", ["type"])
    op.create_index("ix_notifications_is_read", "notifications", ["is_read"])

    op.create_table(
        "user_preferences",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("notification_settings", sa.JSON(), nullable=True),
        sa.Column("profile_visible", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("show_online_status", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("language", sa.String(10), nullable=False, server_default="zh-CN"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE",
            name="fk_user_preferences_user_id_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_preferences"),
        sa.UniqueConstraint("user_id", name="uq_user_preferences_user_id"),
    )
    op.create_index("ix_user_preferences_user_id", "user_preferences", ["user_id"])


def downgrade() -> None:
    op.drop_table("user_preferences")
    op.drop_table("notifications")
    notification_type_enum.drop(op.get_bind(), checkfirst=True)
