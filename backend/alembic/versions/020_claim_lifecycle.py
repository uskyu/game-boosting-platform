"""Claim-level delivery/settlement lifecycle and per-booster wallet settlement.

- order_claims gains status/delivery_note/delivery_attachments/delivered_at/settled_at
- historical order-level delivery data is copied onto the booster's claim
- claims are backfilled for orders assigned without a claim (admin dispatch hole)
- orders.claimed_count is re-synced with the real claim rows
- wallet_transactions gains booster_id and a (order_id, booster_id, type)
  unique key so each booster settles at most once per order
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "020_claim_lifecycle"
down_revision: Union[str, None] = "019_delivery_note"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- order_claims lifecycle columns -------------------------------------
    op.add_column(
        "order_claims",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="CLAIMED"),
    )
    op.add_column("order_claims", sa.Column("delivery_note", sa.Text(), nullable=True))
    op.add_column("order_claims", sa.Column("delivery_attachments", sa.JSON(), nullable=True))
    op.add_column("order_claims", sa.Column("delivered_at", sa.DateTime(), nullable=True))
    op.add_column("order_claims", sa.Column("settled_at", sa.DateTime(), nullable=True))

    # DELIVERED orders: the assigned booster's claim is DELIVERED with the
    # order-level delivery payload copied over.
    op.execute(
        """
        UPDATE order_claims c
        JOIN orders o ON o.id = c.order_id
        SET c.status = 'DELIVERED',
            c.delivery_note = o.delivery_note,
            c.delivery_attachments = o.delivery_attachments,
            c.delivered_at = o.delivered_at
        WHERE o.status = 'DELIVERED' AND o.booster_id = c.booster_id
        """
    )

    # COMPLETED orders: the assigned booster's claim is SETTLED.
    op.execute(
        """
        UPDATE order_claims c
        JOIN orders o ON o.id = c.order_id
        SET c.status = 'SETTLED',
            c.delivery_note = o.delivery_note,
            c.delivery_attachments = o.delivery_attachments,
            c.delivered_at = o.delivered_at,
            c.settled_at = o.completed_at
        WHERE o.status = 'COMPLETED' AND o.booster_id = c.booster_id
        """
    )

    # Historical hole: admin assign_order (and service-card orders) set
    # booster_id without creating an OrderClaim row. Backfill one claim per
    # such order so claim-level delivery keeps working for them.
    op.execute(
        """
        INSERT INTO order_claims
            (order_id, booster_id, status, delivery_note, delivery_attachments,
             delivered_at, settled_at, created_at)
        SELECT
            o.id,
            o.booster_id,
            CASE o.status
                WHEN 'COMPLETED' THEN 'SETTLED'
                WHEN 'DELIVERED' THEN 'DELIVERED'
                ELSE 'CLAIMED'
            END,
            CASE WHEN o.status IN ('DELIVERED', 'COMPLETED') THEN o.delivery_note ELSE NULL END,
            CASE WHEN o.status IN ('DELIVERED', 'COMPLETED') THEN o.delivery_attachments ELSE NULL END,
            CASE WHEN o.status IN ('DELIVERED', 'COMPLETED') THEN o.delivered_at ELSE NULL END,
            CASE WHEN o.status = 'COMPLETED' THEN o.completed_at ELSE NULL END,
            COALESCE(o.locked_at, o.created_at)
        FROM orders o
        WHERE o.booster_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM order_claims c
              WHERE c.order_id = o.id AND c.booster_id = o.booster_id
          )
        """
    )

    # Keep claimed_count in sync with the real claim rows.
    op.execute(
        """
        UPDATE orders o
        SET o.claimed_count = (
            SELECT COUNT(*) FROM order_claims c WHERE c.order_id = o.id
        )
        """
    )

    # --- wallet_transactions per-booster settlement -------------------------
    op.add_column("wallet_transactions", sa.Column("booster_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_wallet_tx_booster",
        "wallet_transactions",
        "users",
        ["booster_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE wallet_transactions wt
        JOIN orders o ON o.id = wt.order_id
        SET wt.booster_id = o.booster_id
        WHERE wt.booster_id IS NULL AND wt.order_id IS NOT NULL
        """
    )
    # MySQL stores UNIQUE constraints as unique indexes: DROP INDEX is the
    # portable way to remove the old (order_id, type) key.
    op.execute("ALTER TABLE wallet_transactions DROP INDEX uq_wallet_tx_order_type")
    op.create_unique_constraint(
        "uq_wallet_tx_order_booster_type",
        "wallet_transactions",
        ["order_id", "booster_id", "type"],
    )


def downgrade() -> None:
    # The data backfills above are not reversible; downgrade only restores
    # the previous schema shape.
    op.drop_constraint(
        "uq_wallet_tx_order_booster_type", "wallet_transactions", type_="unique"
    )
    op.execute(
        "ALTER TABLE wallet_transactions ADD UNIQUE KEY uq_wallet_tx_order_type (order_id, type)"
    )
    op.drop_constraint("fk_wallet_tx_booster", "wallet_transactions", type_="foreignkey")
    op.drop_column("wallet_transactions", "booster_id")
    op.drop_column("order_claims", "settled_at")
    op.drop_column("order_claims", "delivered_at")
    op.drop_column("order_claims", "delivery_attachments")
    op.drop_column("order_claims", "delivery_note")
    op.drop_column("order_claims", "status")
