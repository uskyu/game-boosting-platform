"""add reputation/credit fields to users table

Revision ID: 010_add_reputation_fields
Revises: 009_add_delivered_status
Create Date: 2026-04-09_01_00_00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "010_add_reputation_fields"
down_revision: Union[str, None] = "009_add_delivered_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("credit_score", sa.Integer(), nullable=False, server_default="100"))
    op.add_column("users", sa.Column("credit_level", sa.String(20), nullable=False, server_default="bronze"))
    op.add_column("users", sa.Column("total_completed", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("total_disputed", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("completion_rate", sa.Numeric(precision=5, scale=2), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("avg_rating", sa.Numeric(precision=3, scale=2), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("avg_response_minutes", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("badge_tags", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "badge_tags")
    op.drop_column("users", "avg_response_minutes")
    op.drop_column("users", "avg_rating")
    op.drop_column("users", "completion_rate")
    op.drop_column("users", "total_disputed")
    op.drop_column("users", "total_completed")
    op.drop_column("users", "credit_level")
    op.drop_column("users", "credit_score")
