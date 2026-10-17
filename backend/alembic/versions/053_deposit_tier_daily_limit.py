"""deposit tier daily accept limit

Revision ID: 053_deposit_tier_daily_limit
Revises: 052_order_after_sales
Create Date: 2026-10-16 09:00:00.000000

权益阶梯新增「每日接单量上限」：按北京时间每天 12:00 刷新，窗口内接单且
订单未取消才计数；NULL/0 表示不限。存量行全部补 NULL（不限），上线本身
不改变任何现有行为。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '053_deposit_tier_daily_limit'
down_revision = '052_order_after_sales'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('deposit_tiers', sa.Column('daily_accept_limit', sa.Integer(), nullable=True))


def downgrade():
    op.drop_column('deposit_tiers', 'daily_accept_limit')
