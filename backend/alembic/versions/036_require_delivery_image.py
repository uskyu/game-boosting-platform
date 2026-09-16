"""order.require_delivery_image toggle

Revision ID: 036_require_delivery
Revises: 035_hot_path_indexes
Create Date: 2026-09-14 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '036_require_delivery'
down_revision = '035_hot_path_indexes'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'orders',
        sa.Column('require_delivery_image', sa.Boolean(), nullable=False, server_default=sa.text('0')),
    )


def downgrade():
    op.drop_column('orders', 'require_delivery_image')
