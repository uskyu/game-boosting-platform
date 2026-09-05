"""create order templates

Revision ID: 025_order_templates
Revises: 024_user_restrictions
"""
from alembic import op
import sqlalchemy as sa

revision = "025_order_templates"
down_revision = "024_user_restrictions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_order_templates_user_id", "order_templates", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_order_templates_user_id", table_name="order_templates")
    op.drop_table("order_templates")
