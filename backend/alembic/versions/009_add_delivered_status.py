"""add DELIVERED order status and delivered_at column

Revision ID: 009_add_delivered_status
Revises: 008_unique_constraints
Create Date: 2026-04-09_00_00_00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "009_add_delivered_status"
down_revision: Union[str, None] = "008_unique_constraints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # MySQL ENUM must be rebuilt to add a new value.
    # 1. Add delivered_at column
    op.add_column("orders", sa.Column("delivered_at", sa.DateTime(), nullable=True))

    # 2. Alter the ENUM column to include DELIVERED
    op.alter_column(
        "orders",
        "status",
        existing_type=sa.Enum(
            "PENDING", "LOCKED", "COMPLETED", "DISPUTED", "CANCELLED",
            name="order_status_enum",
        ),
        type_=sa.Enum(
            "PENDING", "LOCKED", "DELIVERED", "COMPLETED", "DISPUTED", "CANCELLED",
            name="order_status_enum",
        ),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Revert: move any DELIVERED orders back to LOCKED before shrinking ENUM
    op.execute("UPDATE orders SET status = 'LOCKED' WHERE status = 'DELIVERED'")

    op.alter_column(
        "orders",
        "status",
        existing_type=sa.Enum(
            "PENDING", "LOCKED", "DELIVERED", "COMPLETED", "DISPUTED", "CANCELLED",
            name="order_status_enum",
        ),
        type_=sa.Enum(
            "PENDING", "LOCKED", "COMPLETED", "DISPUTED", "CANCELLED",
            name="order_status_enum",
        ),
        existing_nullable=False,
    )

    op.drop_column("orders", "delivered_at")
