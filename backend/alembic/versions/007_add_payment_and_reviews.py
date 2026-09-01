"""add payment fields and reviews table

Revision ID: 007_add_payment_and_reviews
Revises: 006_create_booster_service_table
Create Date: 2026-04-03_00_00_00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "007_add_payment_and_reviews"
down_revision: Union[str, None] = "006_create_booster_service_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Payment fields on orders ---
    payment_enum = sa.Enum("UNPAID", "PAID", "REFUNDED", name="payment_status_enum")
    payment_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "orders",
        sa.Column(
            "payment_status",
            payment_enum,
            server_default="UNPAID",
            nullable=False,
        ),
    )
    op.add_column(
        "orders",
        sa.Column("paid_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_orders_payment_status", "orders", ["payment_status"])

    # --- Reviews table ---
    op.create_table(
        "reviews",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "reviewer_id", name="uq_reviews_order_reviewer"),
    )
    op.create_index("ix_reviews_order_id", "reviews", ["order_id"])
    op.create_index("ix_reviews_target_id", "reviews", ["target_id"])


def downgrade() -> None:
    op.drop_table("reviews")
    op.drop_index("ix_orders_payment_status", table_name="orders")
    op.drop_column("orders", "paid_at")
    op.drop_column("orders", "payment_status")
    sa.Enum(name="payment_status_enum").drop(op.get_bind(), checkfirst=True)
