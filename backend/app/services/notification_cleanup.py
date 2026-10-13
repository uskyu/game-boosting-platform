"""Batched cleanup of expired new-order broadcast notifications.

Publishing an order writes one broadcast notification per active non-admin
user, so the table grows by hundreds of thousands of rows per day. Left
unbounded it slows every insert and every read on that table. Only the
new-order broadcasts are pruned: other notification types and system
announcements are kept. Deletion happens in small transactions so a single
cleanup never holds a long-running lock.
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def cleanup_expired_notifications(
    session: AsyncSession,
    *,
    retention_days: int,
    batch_size: int = 5000,
) -> int:
    """Delete notifications older than ``retention_days`` in bounded batches.

    Returns the total number of deleted rows. Each batch commits on its own so
    the lock is released between batches; the loop stops as soon as a batch
    deletes fewer rows than requested (i.e. nothing left to remove).
    """
    if retention_days <= 0:
        return 0

    batch_size = max(1, min(int(batch_size), 50_000))
    # 只清理「新订单发布」广播：type=SYSTEM_ANNOUNCEMENT 且 link 指向订单。
    # 接单/交付/取消等其他类型通知和系统公告一律保留。
    # retention_days/batch_size 是服务端控制的整数，绝不来自用户输入。
    statement = text(
        f"DELETE FROM notifications "
        f"WHERE type='SYSTEM_ANNOUNCEMENT' AND link LIKE '/orders/%' "
        f"AND created_at < DATE_SUB(NOW(), INTERVAL {int(retention_days)} DAY) "
        f"LIMIT {batch_size}"
    )

    deleted_total = 0
    while True:
        result = await session.execute(statement)
        await session.commit()
        deleted = result.rowcount or 0
        deleted_total += deleted
        if deleted < batch_size:
            break

    if deleted_total:
        logger.info(
            "Notification cleanup removed %s rows older than %s days",
            deleted_total,
            retention_days,
        )
    return deleted_total
