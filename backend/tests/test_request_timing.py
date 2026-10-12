import logging

import pytest
from starlette.requests import Request

from app.core.request_timing import (
    RequestTimingMiddleware,
    STAGE_ACCEPT_SERVICE,
    match_route_template,
    measure_stage,
)


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/api/v1/orders/create", "/api/v1/orders/create"),
        ("PUT", "/api/v1/orders/123/accept", "/api/v1/orders/{order_id}/accept"),
        (
            "POST",
            "/api/v1/orders/987654/attachments",
            "/api/v1/orders/{order_id}/attachments",
        ),
        ("GET", "/api/v1/orders", "/api/v1/orders"),
        ("GET", "/api/v1/orders/", "/api/v1/orders"),
    ],
)
def test_match_route_template_uses_fixed_route_labels(method, path, expected):
    assert match_route_template(method, path) == expected
    if "{" in expected:
        assert path.split("/")[-2] not in expected


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/users/123"),
        ("POST", "/api/v1/orders/123/accept"),
        ("PUT", "/api/v1/orders/123/attachments"),
        ("GET", "/api/v1/orders/123"),
        ("GET", "/api/v1/orders?user_id=123"),
    ],
)
def test_match_route_template_ignores_unselected_routes(method, path):
    assert match_route_template(method, path) is None


@pytest.mark.asyncio
async def test_request_timing_adds_server_timing_and_logs_only_route_template(caplog):
    sent = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "PUT",
        "scheme": "http",
        "path": "/api/v1/orders/456789/accept",
        "raw_path": b"/api/v1/orders/456789/accept",
        "query_string": b"access_token=not-for-logs",
        "headers": [],
        "server": ("test", 80),
        "client": ("127.0.0.1", 50000),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def app(inner_scope, inner_receive, send):
        request = Request(inner_scope, receive=inner_receive)
        async with measure_stage(request, STAGE_ACCEPT_SERVICE):
            pass
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"server-timing", b"db;dur=1.2")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(message):
        sent.append(message)

    middleware = RequestTimingMiddleware(app)
    with caplog.at_level(logging.INFO, logger="app.core.request_timing"):
        await middleware(scope, receive, send)

    start = next(message for message in sent if message["type"] == "http.response.start")
    headers = dict(start["headers"])
    assert headers[b"server-timing"].startswith(b"db;dur=1.2, response_start;dur=")
    assert b"accept_service;dur=" in headers[b"server-timing"]

    records = [record for record in caplog.records if getattr(record, "event", None) == "request_timing"]
    assert len(records) == 1
    record = records[0]
    assert record.route == "/api/v1/orders/{order_id}/accept"
    assert record.method == "PUT"
    assert record.status == 200
    assert record.duration_ms >= 0
    assert "456789" not in record.getMessage()
    assert "not-for-logs" not in record.getMessage()


@pytest.mark.asyncio
async def test_request_timing_skips_unselected_routes(caplog):
    sent = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/v1/users/98765",
        "raw_path": b"/api/v1/users/98765",
        "query_string": b"",
        "headers": [],
        "server": ("test", 80),
        "client": ("127.0.0.1", 50000),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def app(inner_scope, inner_receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(message):
        sent.append(message)

    with caplog.at_level(logging.INFO, logger="app.core.request_timing"):
        await RequestTimingMiddleware(app)(scope, receive, send)

    start = next(message for message in sent if message["type"] == "http.response.start")
    assert b"server-timing" not in dict(start["headers"])
    assert not [record for record in caplog.records if getattr(record, "event", None) == "request_timing"]
