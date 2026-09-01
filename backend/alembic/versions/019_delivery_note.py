"""Add delivery_note column for booster's end-of-order report text."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "019_delivery_note"
down_revision: Union[str, None] = "018_deliver_attachments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("delivery_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "delivery_note")
