"""Create the single-row site settings table."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "016_site_settings"
down_revision: Union[str, None] = "015_order_dispatch_controls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "site_settings",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("site_name", sa.String(length=200), nullable=False, server_default="游戏服务平台"),
        sa.Column("site_description", sa.Text(), nullable=True),
        sa.Column("site_logo_url", sa.String(length=500), nullable=True),
        sa.Column("favicon_url", sa.String(length=500), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("site_settings")
