"""Add order titles, price ranges, attachments, and dispatch controls."""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "015_order_dispatch_controls"
down_revision: Union[str, None] = "014_game_assets_and_bulk"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column("orders", sa.Column("title", sa.String(200), nullable=True))
    op.add_column("orders", sa.Column("intro", sa.Text(), nullable=True))
    op.add_column("orders", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("orders", sa.Column("price_min", sa.Numeric(10, 2), nullable=True))
    op.add_column("orders", sa.Column("price_max", sa.Numeric(10, 2), nullable=True))
    op.add_column("orders", sa.Column("max_claims", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("orders", sa.Column("claimed_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("claim_status", sa.Enum("OPEN", "PAUSED", "FULL", "CLOSED", name="claim_status_enum"), nullable=False, server_default="OPEN"))
    op.add_column("orders", sa.Column("deadline", sa.DateTime(), nullable=True))
    op.add_column("orders", sa.Column("is_archived", sa.Boolean(), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("attachments", sa.JSON(), nullable=True))
    op.execute("UPDATE orders SET price_min = price, price_max = price, claimed_count = CASE WHEN booster_id IS NULL THEN 0 ELSE 1 END")
    op.create_table("order_claims",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("booster_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("order_id", "booster_id", name="uq_order_claim_booster"),
    )
    op.execute("INSERT INTO order_claims (order_id, booster_id) SELECT id, booster_id FROM orders WHERE booster_id IS NOT NULL")

def downgrade() -> None:
    op.drop_table("order_claims")
    for name in ("attachments", "is_archived", "deadline", "claim_status", "claimed_count", "max_claims", "price_max", "price_min", "description", "intro", "title"):
        op.drop_column("orders", name)
