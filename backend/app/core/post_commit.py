"""Post-commit hook registry.

通知/广播这类「业务成功才该发生」的副作用先登记，等请求的数据库事务
真正 commit 之后再执行：订单行锁与连接池不再被 WebSocket 扇出拖住
（扇出可能要串行/并发等几十个慢客户端），回滚时副作用自动作废。

请求上下文之外（结算调度器、定时任务）没有注册表，调用即时执行，
行为与原来的内联发送一致。
"""

import contextvars
import logging
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)

_registry: contextvars.ContextVar[list[Callable[[], Coroutine[Any, Any, None]]] | None] = (
    contextvars.ContextVar("post_commit_hooks", default=None)
)


def start_registry() -> contextvars.Token:
    """开启当前请求的钩子登记表（依赖注入 yield 前调用）。"""
    return _registry.set([])


def stop_registry(token: contextvars.Token) -> None:
    """关闭登记表（依赖注入 finally 中调用）。"""
    _registry.reset(token)


async def run_after_commit(factory: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """登记一个 commit 成功后才执行的协程工厂；无注册表时立即执行。"""
    registry = _registry.get()
    if registry is None:
        await factory()
        return
    registry.append(factory)


async def drain_registry() -> None:
    """commit 成功后调用：执行并清空已登记的钩子，单个失败不影响其余。"""
    registry = _registry.get()
    if not registry:
        return
    pending = list(registry)
    registry.clear()
    for factory in pending:
        try:
            await factory()
        except Exception:
            logger.warning("post-commit hook failed", exc_info=True)
