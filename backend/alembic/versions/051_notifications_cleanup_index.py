"""composite index for the expired-notification cleanup hot path

Revision ID: 051_notifications_cleanup_index
Revises: 050_notifications_user_indexes
Create Date: 2026-10-15 10:30:00.000000

通知清理任务每 6h 跑一次，WHERE 形如
``type='SYSTEM_ANNOUNCEMENT' AND link LIKE '/orders/%' AND created_at < ...``，
此前只能命中 ix_notifications_type（type 单列）后回表逐行过滤 created_at，
在 54 万行表上是无索引全表扫分批删。补 (type, created_at)：type 等值走到
有序前缀，created_at 范围在索引内完成，最小面覆盖清理谓词。
按「列集合」判重，避免与任何命名的等价索引重复。
"""
from alembic import op
from sqlalchemy import inspect as sa_inspect


# revision identifiers, used by Alembic.
revision = '051_notifications_cleanup_index'
down_revision = '050_notifications_user_indexes'
branch_labels = None
depends_on = None

_INDEX_NAME = 'ix_notifications_type_created'
_TARGET_COLUMNS = ('type', 'created_at')


def _has_index_on_target_columns(bind) -> bool:
    """列集合相同的索引已存在（任何命名）就不重复建：重复索引只增加写入放大。"""
    for index in sa_inspect(bind).get_indexes('notifications'):
        if tuple(index.get('column_names') or ()) == _TARGET_COLUMNS:
            return True
    return False


def upgrade():
    bind = op.get_bind()
    existing = {index['name'] for index in sa_inspect(bind).get_indexes('notifications')}
    if _INDEX_NAME in existing or _has_index_on_target_columns(bind):
        return
    op.create_index(_INDEX_NAME, 'notifications', list(_TARGET_COLUMNS))


def downgrade():
    bind = op.get_bind()
    existing = {index['name'] for index in sa_inspect(bind).get_indexes('notifications')}
    if _INDEX_NAME in existing:
        op.drop_index(_INDEX_NAME, table_name='notifications')
