"""Add platform announcements and per-user display tracking."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "037_announcements"
down_revision: Union[str, None] = "036_require_delivery"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "announcements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content_html", sa.Text(), nullable=False),
        sa.Column("safe_content_html", sa.Text(), nullable=False),
        sa.Column(
            "frequency",
            sa.Enum("EVERY_OPEN", "DAILY", "ONCE", name="announcement_frequency_enum"),
            nullable=False,
        ),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("start_at", sa.DateTime(), nullable=True),
        sa.Column("end_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_announcements_created_by_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_announcements_id", "announcements", ["id"])
    op.create_index("ix_announcements_is_enabled", "announcements", ["is_enabled"])
    op.create_index("ix_announcements_priority", "announcements", ["priority"])
    op.create_index("ix_announcements_start_at", "announcements", ["start_at"])
    op.create_index("ix_announcements_end_at", "announcements", ["end_at"])

    op.create_table(
        "announcement_views",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("announcement_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("last_shown_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["announcement_id"], ["announcements.id"], name="fk_announcement_views_announcement_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_announcement_views_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("announcement_id", "user_id", name="uq_announcement_views_announcement_user"),
    )
    op.create_index("ix_announcement_views_announcement_id", "announcement_views", ["announcement_id"])
    op.create_index("ix_announcement_views_user_id", "announcement_views", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_announcement_views_user_id", table_name="announcement_views")
    op.drop_index("ix_announcement_views_announcement_id", table_name="announcement_views")
    op.drop_table("announcement_views")
    op.drop_index("ix_announcements_end_at", table_name="announcements")
    op.drop_index("ix_announcements_start_at", table_name="announcements")
    op.drop_index("ix_announcements_priority", table_name="announcements")
    op.drop_index("ix_announcements_is_enabled", table_name="announcements")
    op.drop_index("ix_announcements_id", table_name="announcements")
    op.drop_table("announcements")
