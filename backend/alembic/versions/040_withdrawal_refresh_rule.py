"""withdrawal refresh rule settings

Revision ID: 040_withdrawal_refresh_rule
Revises: 039_global_service_fee
Create Date: 2026-09-27 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '040_withdrawal_refresh_rule'
down_revision = '039_global_service_fee'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'withdrawal_rule_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('mode', sa.Enum('INTERVAL', 'DAILY_NOON', name='withdrawal_refresh_mode_enum'), nullable=False, server_default='INTERVAL'),
        sa.Column('interval_hours', sa.Integer(), nullable=False, server_default='24'),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('withdrawal_rule_settings')
