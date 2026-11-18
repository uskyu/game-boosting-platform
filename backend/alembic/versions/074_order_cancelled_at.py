"""Add a stable timestamp for order cancellation reporting.

Revision ID: 074_order_cancelled_at
Revises: 073_order_designated_booster
Create Date: 2026-11-18 00:00:00.000000

Existing cancelled orders are backfilled from ``updated_at``. That gives old
rows a useful approximate timestamp; newly cancelled orders record the exact
transition time in the application.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "074_order_cancelled_at"
down_revision: str | None = "073_order_designated_booster"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_orders_cancelled_at"


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
    )
    op.create_index(_INDEX, "orders", ["cancelled_at"], unique=False)
    op.execute(
        sa.text("UPDATE orders SET cancelled_at = updated_at WHERE status = 'CANCELLED' AND cancelled_at IS NULL")
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="orders")
    op.drop_column("orders", "cancelled_at")
