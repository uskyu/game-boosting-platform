"""Low-cardinality timing for the selected order API routes."""

import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

TIMING_STATE_KEY = "order_request_timing_stages"

STAGE_ORDER_SERVICE = "order_service"
STAGE_ORDER_NOTIFICATIONS = "order_notifications"
STAGE_NOTIFICATION_RECIPIENTS = "notification_recipients"
STAGE_NOTIFICATION_PREFERENCES = "notification_preferences"
STAGE_NOTIFICATION_PREPARE = "notification_prepare"
STAGE_NOTIFICATION_INSERT = "notification_insert"
STAGE_NOTIFICATION_PAYLOADS = "notification_payloads"
STAGE_ACCEPT_SERVICE = "accept_service"
STAGE_ACCEPT_SIDE_EFFECTS = "accept_side_effects"

_ALLOWED_STAGES = frozenset(
    {
        STAGE_ORDER_SERVICE,
        STAGE_ORDER_NOTIFICATIONS,
        STAGE_NOTIFICATION_RECIPIENTS,
        STAGE_NOTIFICATION_PREFERENCES,
        STAGE_NOTIFICATION_PREPARE,
        STAGE_NOTIFICATION_INSERT,
        STAGE_NOTIFICATION_PAYLOADS,
        STAGE_ACCEPT_SERVICE,
        STAGE_ACCEPT_SIDE_EFFECTS,
    }
)

_STATIC_ROUTES = {
    ("POST", "/api/v1/orders/create"): "/api/v1/orders/create",
    ("GET", "/api/v1/orders"): "/api/v1/orders",
    ("GET", "/api/v1/orders/"): "/api/v1/orders",
    # 大厅摘要：前端大厅的轮询心跳（默认 10s 一次），线上调用量全站第一
    # （4 万请求里占 23%）。不埋点就等于对这条最高频链路完全没有耗时视图。
    ("GET", "/api/v1/orders/hall-digest"): "/api/v1/orders/hall-digest",
    # 我的订单：曾因 Wallet.transactions 的 selectin 与读路径上的 FOR UPDATE
    # 出现 500ms+ 的尾延迟，需要长期观测。
    ("GET", "/api/v1/orders/claims/mine"): "/api/v1/orders/claims/mine",
}
_ORDER_ID_ROUTES = (
    (
        "PUT",
        re.compile(r"/api/v1/orders/[0-9]+/accept"),
        "/api/v1/orders/{order_id}/accept",
    ),
    (
        "POST",
        re.compile(r"/api/v1/orders/[0-9]+/attachments"),
        "/api/v1/orders/{order_id}/attachments",
    ),
)


def match_route_template(method: str, path: str) -> str | None:
    """Return a fixed template for an observed route, never a concrete path."""
    template = _STATIC_ROUTES.get((method, path))
    if template is not None:
        return template

    for route_method, pattern, route_template in _ORDER_ID_ROUTES:
        if method == route_method and pattern.fullmatch(path):
            return route_template
    return None


@asynccontextmanager
async def measure_stage(request: Request, stage_name: str) -> AsyncIterator[None]:
    """Measure one approved endpoint stage and attach it to request state."""
    if stage_name not in _ALLOWED_STAGES:
        raise ValueError("Unsupported request timing stage")

    started = time.perf_counter()
    try:
        yield
    finally:
        stages = getattr(request.state, TIMING_STATE_KEY, None)
        if isinstance(stages, list):
            stages.append((stage_name, max(0.0, (time.perf_counter() - started) * 1000)))


class RequestTimingMiddleware:
    """Measure only fixed order routes without retaining or logging raw paths."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        route_template = match_route_template(method, scope.get("path", ""))
        if route_template is None:
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        stages: list[tuple[str, float]] = []
        scope.setdefault("state", {})[TIMING_STATE_KEY] = stages
        response_status = 500
        response_started = False

        async def send_with_timing(message: Message) -> None:
            nonlocal response_status, response_started
            if message["type"] == "http.response.start" and not response_started:
                response_started = True
                response_status = int(message.get("status", 500))
                app_duration_ms = max(0.0, (time.perf_counter() - started) * 1000)
                # Header is sent before dependency teardown/commit can finish;
                # label this boundary honestly rather than calling it full request time.
                timing_items = [f"response_start;dur={app_duration_ms:.3f}"]
                timing_items.extend(
                    f"{name};dur={duration_ms:.3f}" for name, duration_ms in stages
                )
                _append_server_timing(message, timing_items)
            await send(message)

        try:
            await self.app(scope, receive, send_with_timing)
        finally:
            duration_ms = max(0.0, (time.perf_counter() - started) * 1000)
            safe_stages = [
                {"name": name, "duration_ms": round(stage_duration, 3)}
                for name, stage_duration in stages
                if name in _ALLOWED_STAGES
            ]
            stages_json = json.dumps(safe_stages, separators=(",", ":"))
            scope_state = scope.get("state", {})
            safe_counts = scope_state.get(TIMING_STATE_KEY + "_counts", {})
            safe_counts = {
                name: max(0, int(value))
                for name, value in safe_counts.items()
                if name in {"recipient_count", "preference_count", "notification_count"}
            }
            counts_json = json.dumps(safe_counts, separators=(",", ":"))
            logger.info(
                "request_timing method=%s route=%s status=%d duration_ms=%.3f stages=%s counts=%s",
                method,
                route_template,
                response_status,
                duration_ms,
                stages_json,
                counts_json,
                extra={
                    "event": "request_timing",
                    "method": method,
                    "route": route_template,
                    "status": response_status,
                    "duration_ms": round(duration_ms, 3),
                    "stages": safe_stages,
                    "counts": safe_counts,
                },
            )


def _append_server_timing(message: Message, timing_items: list[str]) -> None:
    """Preserve existing Server-Timing values and append valid comma-separated items."""
    existing: list[str] = []
    other_headers: list[tuple[bytes, bytes]] = []
    for name, value in message.get("headers", []):
        if name.lower() == b"server-timing":
            existing.append(value.decode("latin-1"))
        else:
            other_headers.append((name, value))

    values = [value for value in existing if value]
    values.extend(timing_items)
    other_headers.append((b"server-timing", ", ".join(values).encode("latin-1")))
    message["headers"] = other_headers
