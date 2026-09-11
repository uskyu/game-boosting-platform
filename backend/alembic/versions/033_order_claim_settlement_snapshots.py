"""Snapshot claim settlement terms at delivery/approval.

New claims keep the settlement mode, tier duration, due time, and reviewer
terms on the claim itself. Claims created before this migration remain
backward-compatible: when no snapshot exists, the scheduler falls back to the
order's payout_delay_days / payout_delay_hours fields.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "033_claim_settlement_snap"
down_revision: Union[str, None] = "032_deposit_tiers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMNS = (
    ("approved_payout_amount", sa.Numeric(precision=12, scale=2)),
    ("approved_deduction", sa.Numeric(precision=12, scale=2)),
    ("approved_note", sa.String(length=500)),
    ("settlement_mode_snapshot", sa.String(length=20)),
    ("settle_hours_snapshot", sa.Integer()),
    ("settlement_due_at", sa.DateTime()),
)


def _existing_columns() -> set[str]:
    bind = op.get_bind()
    return {column["name"] for column in sa.inspect(bind).get_columns("order_claims")}


def upgrade() -> None:
    existing = _existing_columns()
    for name, column_type in _COLUMNS:
        if name not in existing:
            op.add_column(
                "order_claims",
                sa.Column(name, column_type, nullable=True),
            )


def downgrade() -> None:
    existing = _existing_columns()
    for name, _column_type in reversed(_COLUMNS):
        if name in existing:
            op.drop_column("order_claims", name)
