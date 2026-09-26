"""order.service_fee_rate per-order service fee

Revision ID: 038_order_service_fee
Revises: 037_announcements
Create Date: 2026-09-26 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '038_order_service_fee'
down_revision = '037_announcements'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'orders',
        sa.Column('service_fee_rate', sa.Numeric(precision=5, scale=2), nullable=True),
    )


def downgrade():
    op.drop_column('orders', 'service_fee_rate')
