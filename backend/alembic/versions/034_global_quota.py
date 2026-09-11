"""add global booster quota

Revision ID: 034_global_quota
Revises: 033_claim_settlement_snap
Create Date: 2026-09-11 17:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '034_global_quota'
down_revision = '033_claim_settlement_snap'
branch_labels = None
depends_on = None


def upgrade():
    # 直接复用 deposit_settings 单行表（id=1），加一个全局配额字段
    # 默认 5：所有用户同时最多处理 5 个未完成订单
    op.add_column(
        'deposit_settings',
        sa.Column('global_booster_quota', sa.Integer(), nullable=False, server_default='5')
    )


def downgrade():
    op.drop_column('deposit_settings', 'global_booster_quota')
