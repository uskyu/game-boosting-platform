"""Add delivery attachments JSON column for booster proof images."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "018_deliver_attachments"
down_revision: Union[str, None] = "017_chat_pinning"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("delivery_attachments", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "delivery_attachments")
