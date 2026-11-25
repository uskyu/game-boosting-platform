"""Batched cleanup of expired new-order broadcast notifications.

Publishing an order writes one broadcast notification per active non-admin
user, so the table grows by hundreds of thousands of rows per day. Left
unbounded it slows every insert and every read on that table. Only the
new-order broadcasts are pruned: other notification types and system
announcements are kept. Deletion happens in small transactions so a single
cleanup never holds a long-running lock.
"""

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# 每批删除提交后主动让路的时间（秒）。见函数内注释。
_BATCH_YIELD_SECONDS = 0.15


async def cleanup_expired_notifications(
    session: AsyncSession,
    *,
    retention_days: int,
    batch_size: int = 2000,
) -> int:
    """Delete notifications older than ``retention_days`` in bounded batches.

    Returns the total number of deleted rows. Each batch commits on its own so
    the lock is released between batches; the loop stops as soon as a batch
    deletes fewer rows than requested (i.e. nothing left to remove).
    """
    if retention_days <= 0:
        return 0

    batch_size = max(1, min(int(batch_size), 50_000))
    # 为什么小批次 + 批间让路：生产一次清理曾删 8.9 万~120 万行，单条大 DELETE
    # 长时间持锁并占满 IO，期间的发单插入从 1 秒退化到 6~15 秒。改成每批最多
    # batch_size 行、提交后 sleep 0.15 秒，把锁和 IO 切成短片段，让发单插入在
    # 批次之间有机会执行；循环到某批删不满 batch_size 即说明已清空。
    #
    # 为什么保留 link 过滤：SYSTEM_ANNOUNCEMENT 里既有「新订单发布」广播，也有
    # 管理员发的真正系统公告。只有 link 指向 /orders/ 的才是可清理的广播，漏掉
    # 这个过滤会把真正的系统公告一起删掉。
    #
    # 索引：type 等值 + created_at 范围可走 ix_notifications_type_created；
    # link LIKE 只作为回表后的行过滤，created_at 不套函数，避免退化为全表扫描。
    # retention_days/batch_size 是服务端控制的整数，绝不来自用户输入。
    statement = text(
        f"DELETE FROM notifications "
        f"WHERE type='SYSTEM_ANNOUNCEMENT' "
        f"AND created_at < DATE_SUB(NOW(), INTERVAL {int(retention_days)} DAY) "
        f"AND link LIKE '/orders/%' "
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
        await asyncio.sleep(_BATCH_YIELD_SECONDS)

    if deleted_total:
        logger.info(
            "Notification cleanup removed %s rows older than %s days",
            deleted_total,
            retention_days,
        )
    return deleted_total
