"""composite index for the notification list polling hot path

Revision ID: 050_notifications_user_indexes
Revises: 049_notifications_ref_id
Create Date: 2026-10-14 22:10:00.000000

WS 断线时客户端每 2 秒轮询 /notifications，列表查询是
``WHERE user_id=? ORDER BY created_at DESC LIMIT 10``（外加 user_id 等值总数）。
035 已建 (user_id, is_read) 覆盖未读计数，但缺 (user_id, created_at)：列表仍要
回表 + filesort 全量历史行（生产表曾 265 万行），单 worker 因此被轮询打满。
本迁移只补这一只；按「列集合」判重，避免与手工/命名不同的等价索引重复。
"""
from alembic import op
from sqlalchemy import inspect as sa_inspect


# revision identifiers, used by Alembic.
revision = '050_notifications_user_indexes'
down_revision = '049_notifications_ref_id'
branch_labels = None
depends_on = None

_TARGET_COLUMNS = ('user_id', 'created_at')


def _has_index_on_target_columns(bind) -> bool:
    """列集合相同的索引已存在（任何命名）就不重复建：重复索引只增加写入放大。"""
    for index in sa_inspect(bind).get_indexes('notifications'):
        if tuple(index.get('column_names') or ()) == _TARGET_COLUMNS:
            return True
    return False


def upgrade():
    bind = op.get_bind()
    existing = {index['name'] for index in sa_inspect(bind).get_indexes('notifications')}
    if 'ix_notifications_user_created' in existing or _has_index_on_target_columns(bind):
        return
    op.create_index(
        'ix_notifications_user_created',
        'notifications',
        list(_TARGET_COLUMNS),
    )


def downgrade():
    bind = op.get_bind()
    existing = {index['name'] for index in sa_inspect(bind).get_indexes('notifications')}
    if 'ix_notifications_user_created' in existing:
        op.drop_index('ix_notifications_user_created', table_name='notifications')
