"""Add game logo asset storage metadata.

Revision ID: 014_game_assets_and_bulk
Revises: 013_deactivate_seed_games
Create Date: 2026-09-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "014_game_assets_and_bulk"
down_revision: Union[str, None] = "013_deactivate_seed_games"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("games", sa.Column("logo_url", sa.String(length=500), nullable=True))
    # Keep the migration-level default aligned with the model and revision 013.
    op.alter_column(
        "games",
        "is_active",
        existing_type=sa.Boolean(),
        existing_nullable=False,
        server_default=sa.text("0"),
    )


def downgrade() -> None:
    op.drop_column("games", "logo_url")
