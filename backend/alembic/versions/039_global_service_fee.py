"""global service fee setting

Revision ID: 039_global_service_fee
Revises: 038_order_service_fee
Create Date: 2026-09-26 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '039_global_service_fee'
down_revision = '038_order_service_fee'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'service_fee_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('service_fee_rate', sa.Numeric(precision=5, scale=2), nullable=False, server_default='0.00'),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('service_fee_settings')
