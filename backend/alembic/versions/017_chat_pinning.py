"""Add per-participant conversation pinning."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "017_chat_pinning"
down_revision: Union[str, None] = "016_site_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversation_participants",
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.add_column(
        "conversation_participants",
        sa.Column("pinned_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversation_participants", "pinned_at")
    op.drop_column("conversation_participants", "is_pinned")
