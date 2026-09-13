"""hot-path indexes for orders list and notifications unread counts

Revision ID: 035_hot_path_indexes
Revises: 034_global_quota
Create Date: 2026-09-13 23:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '035_hot_path_indexes'
down_revision = '034_global_quota'
branch_labels = None
depends_on = None


def upgrade():
    # 订单列表默认按 created_at 倒序翻页；status+created_at 组合让
    # 「按状态筛选 + 排序」免掉 filesort
    op.create_index(
        'ix_orders_status_created_at',
        'orders',
        ['status', sa.text('created_at DESC')],
    )
    op.create_index('ix_orders_created_at', 'orders', ['created_at'])
    # 通知未读数/列表都按 (user_id, is_read) 过滤
    op.create_index(
        'ix_notifications_user_is_read',
        'notifications',
        ['user_id', 'is_read'],
    )


def downgrade():
    op.drop_index('ix_notifications_user_is_read', table_name='notifications')
    op.drop_index('ix_orders_created_at', table_name='orders')
    op.drop_index('ix_orders_status_created_at', table_name='orders')
