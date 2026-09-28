"""withdrawal channel settings（支付宝/微信提现开关，单行，id=1）

Revision ID: 041_withdrawal_channel_settings
Revises: 040_withdrawal_refresh_rule
Create Date: 2026-09-28 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '041_withdrawal_channel_settings'
down_revision = '040_withdrawal_refresh_rule'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'withdrawal_channel_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('alipay_enabled', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('wechat_enabled', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('withdrawal_channel_settings')
