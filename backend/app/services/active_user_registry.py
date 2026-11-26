"""进程内「最近仍在轮询通知」的活跃用户登记表。

背景：公开新订单的实时通知只写给当前 WS 在线用户。手机 WS 被系统挂起或
半开时，服务端已经判定该用户离线，通知行不会落库；客户端恢复后无论怎么
轮询通知都补不回那一条。通知列表 / 未读数接口在每次轮询时调用 ``touch``，
发单 fanout 把「最近窗口内仍在轮询」的用户并入收件人候选，作为 WS 在线
状态之外的第二来源。

约束：仅进程内内存，不写数据库/Redis，不记录 IP/UA。与
``connection_manager`` 相同，本实现假设**单 worker 部署**；如果将来横向扩
容成多 worker，必须换成共享注册表（例如 Redis），否则其他 worker 上收到
的轮询记录对发单 worker 不可见。
"""

import asyncio
from time import monotonic

# 前端 WS 连接态每 30 秒对账一次通知，断线态每 2 秒轮询一次。120 秒足以
# 覆盖短暂网络抖动与后台恢复延迟，同时避免把已离开的用户长期当作收件人。
DEFAULT_ACTIVE_WINDOW_SECONDS = 120.0

# user_id -> 最近一次轮询的 monotonic 时间戳。只在事件循环内读写。
_touch_times: dict[int, float] = {}
_lock = asyncio.Lock()


def _prune_locked(now: float, horizon_seconds: float) -> None:
    """删除已超出保留范围的条目（调用方须已持有 ``_lock``）。

    清理范围至少取默认窗口，保证 ``recent_user_ids`` 用更小窗口查询时，
    默认窗口内的数据不会被顺手删掉；用更大窗口查询时也不会丢数据。
    """
    if not _touch_times:
        return
    cutoff = now - max(horizon_seconds, DEFAULT_ACTIVE_WINDOW_SECONDS)
    expired = [
        user_id
        for user_id, touched_at in _touch_times.items()
        if touched_at <= cutoff
    ]
    for user_id in expired:
        _touch_times.pop(user_id, None)


async def touch(user_id: int) -> None:
    """记录/刷新 ``user_id`` 的最近活跃时间，并顺带清理过期条目。"""
    now = monotonic()
    async with _lock:
        _touch_times[int(user_id)] = now
        _prune_locked(now, DEFAULT_ACTIVE_WINDOW_SECONDS)


async def recent_user_ids(within_seconds: float) -> set[int]:
    """返回最近 ``within_seconds`` 秒内活跃的用户 ID 快照，并顺带清理过期条目。"""
    now = monotonic()
    window = max(float(within_seconds), 0.0)
    async with _lock:
        _prune_locked(now, window)
        cutoff = now - window
        return {
            user_id
            for user_id, touched_at in _touch_times.items()
            if touched_at >= cutoff
        }


def reset() -> None:
    """清空登记表（测试隔离用）。

    同步实现是测试约定：只在没有并发 ``touch`` 的用例准备阶段调用。
    生产代码不应调用。
    """
    _touch_times.clear()
