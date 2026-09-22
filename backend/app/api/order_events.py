"""Post-commit WebSocket events for order state changes."""

from enum import Enum
from typing import Any

from app.core.post_commit import run_after_commit
from app.services.connection_manager import get_connection_manager


def _serialize_value(value: Any) -> Any:
    """Keep enum values JSON-safe without coupling this helper to one model."""
    return value.value if isinstance(value, Enum) else value


async def broadcast_order_state_changed(
    *,
    order_id: int,
    status: Any,
    claim_status: Any = None,
    claimed_count: int | None = None,
) -> None:
    """Notify every connected client that an order's hall state changed.

    The event is registered with the request's post-commit registry. A failed
    order mutation therefore cannot make another user's hall refresh against
    state that was rolled back, and the broadcast never holds an order or
    database lock while sending to WebSocket clients.
    """
    data: dict[str, Any] = {
        "order_id": int(order_id),
        "status": _serialize_value(status),
    }
    if claim_status is not None:
        data["claim_status"] = _serialize_value(claim_status)
    if claimed_count is not None:
        data["claimed_count"] = int(claimed_count)

    payload = {
        "event": "order_state_changed",
        "data": data,
    }
    connection_manager = get_connection_manager()

    async def _broadcast() -> None:
        await connection_manager.broadcast(payload)

    await run_after_commit(_broadcast)
