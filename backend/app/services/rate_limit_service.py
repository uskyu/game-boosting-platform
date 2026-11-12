"""进程内滑动窗口限流。

用法：``check(key, limit, window_seconds)`` 每次请求调用一次，返回
``(allowed, retry_after)``。当前部署是单 uvicorn 进程，内存桶足够；将来
加 worker / 多副本时必须换成 Redis，否则每个进程各自计数，实际额度会被
放大到 worker 数倍。
"""

from __future__ import annotations

import math
import time
from collections import deque

_BUCKET_CLEANUP_INTERVAL = 60.0
_BUCKET_IDLE_SECONDS = 3600.0

# key -> 请求时间戳（monotonic 秒），deque 内按时间递增
_buckets: dict[str, deque[float]] = {}
_last_cleanup = 0.0


def check(key: str, limit: int, window_seconds: float) -> tuple[bool, int]:
    """记录一次请求并判断是否放行。

    Args:
        key: 限流键（用户/订单/场景组合）。
        limit: 窗口内允许的最大请求数。
        window_seconds: 滑动窗口长度。

    Returns:
        ``(True, 0)`` 放行；``(False, retry_after)`` 超限，retry_after 为建议
        等待秒数（至少 1，供 ``Retry-After`` 与前端退避使用）。
    """
    now = time.monotonic()
    bucket = _buckets.get(key)
    if bucket is None:
        bucket = deque()
        _buckets[key] = bucket

    cutoff = now - window_seconds
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()

    if len(bucket) >= limit:
        retry_after = max(1, math.ceil(bucket[0] + window_seconds - now))
        return False, retry_after

    bucket.append(now)
    _cleanup(now)
    return True, 0


def _cleanup(now: float) -> None:
    """低频清理空桶/长期无请求的键，避免用户量增长后字典无限膨胀。"""
    global _last_cleanup
    if now - _last_cleanup < _BUCKET_CLEANUP_INTERVAL:
        return
    _last_cleanup = now
    stale = [
        key
        for key, bucket in _buckets.items()
        if not bucket or bucket[-1] <= now - _BUCKET_IDLE_SECONDS
    ]
    for key in stale:
        _buckets.pop(key, None)


def reset() -> None:
    """清空全部桶（测试之间隔离；也可用于运维热重置）。"""
    global _last_cleanup
    _buckets.clear()
    _last_cleanup = 0.0
