"""API 层限流辅助：统一 429 响应体与 Retry-After 头。

前端接单验证码已经按 ``detail.retry_after`` 自动退避；接单/列表/摘要也
沿用同一结构，避免每个接口各写一套。
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.core.config import settings
from app.services import rate_limit_service


def _raise_rate_limited(retry_after: int, code: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "code": code,
            "message": "操作过于频繁，请稍后再试",
            "retry_after": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )


def enforce_user_limit(
    user_id: int,
    scope: str,
    limit: int,
    window_seconds: float,
    *,
    code: str,
) -> None:
    """按用户+场景限流；``RATE_LIMIT_ENABLED=false`` 时整体跳过。"""
    if not settings.RATE_LIMIT_ENABLED:
        return
    allowed, retry_after = rate_limit_service.check(
        f"{scope}:user:{user_id}", limit, window_seconds
    )
    if not allowed:
        _raise_rate_limited(retry_after, code)


def enforce_key_limit(
    key: str,
    limit: int,
    window_seconds: float,
    *,
    code: str,
) -> None:
    """按任意键限流（例如同一用户对同一订单的接单尝试）。"""
    if not settings.RATE_LIMIT_ENABLED:
        return
    allowed, retry_after = rate_limit_service.check(key, limit, window_seconds)
    if not allowed:
        _raise_rate_limited(retry_after, code)
