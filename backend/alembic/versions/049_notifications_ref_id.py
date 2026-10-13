"""index notifications.ref_id and drop the redundant id index

Revision ID: 049_notifications_ref_id
Revises: 048_order_pre_dispute_status
Create Date: 2026-10-13 10:30:00.000000

"""
from alembic import op
from sqlalchemy import inspect as sa_inspect


# revision identifiers, used by Alembic.
revision = '049_notifications_ref_id'
down_revision = '048_order_pre_dispute_status'
branch_labels = None
depends_on = None


def upgrade():
    # 发单广播要按 ref_id（订单号）定位「这一单发出的通知」；没有该索引时
    # MySQL 只能走 user_id 索引扫海量历史通知行，表越大越慢。
    op.create_index('ix_notifications_ref_id', 'notifications', ['ref_id'])

    # ix_notifications_id 与 PRIMARY(id) 完全重复，纯粹是写放大：每次插入多维护
    # 一棵一样的 B+ 树。确认存在再删，避免环境差异导致迁移失败。
    bind = op.get_bind()
    existing = {index['name'] for index in sa_inspect(bind).get_indexes('notifications')}
    if 'ix_notifications_id' in existing:
        op.drop_index('ix_notifications_id', table_name='notifications')


def downgrade():
    bind = op.get_bind()
    existing = {index['name'] for index in sa_inspect(bind).get_indexes('notifications')}
    if 'ix_notifications_id' not in existing:
        op.create_index('ix_notifications_id', 'notifications', ['id'])
    op.drop_index('ix_notifications_ref_id', table_name='notifications')
