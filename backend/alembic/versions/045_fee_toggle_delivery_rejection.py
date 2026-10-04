"""Add per-order service fee toggle and delivery rejection details.

Revision ID: 045_fee_toggle_delivery_rejection
Revises: 044_cancel_compensation_ledger
Create Date: 2026-10-04 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "045_fee_toggle_delivery_rejection"
down_revision = "044_cancel_compensation_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_fee_settings",
        sa.Column(
            "individual_service_fee_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "order_claims",
        sa.Column("delivery_rejection_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "order_claims",
        sa.Column("delivery_rejected_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("order_claims", "delivery_rejected_at")
    op.drop_column("order_claims", "delivery_rejection_reason")
    op.drop_column("service_fee_settings", "individual_service_fee_enabled")
