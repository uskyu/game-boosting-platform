"""Add qrcode_url column for withdrawal payment QR codes."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "021_withdrawal_qrcode"
down_revision: Union[str, None] = "020_claim_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "withdrawal_requests",
        sa.Column("qrcode_url", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("withdrawal_requests", "qrcode_url")
