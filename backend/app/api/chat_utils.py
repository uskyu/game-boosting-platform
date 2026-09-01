"""
Shared chat utilities for API endpoints.
Provides helpers for broadcasting order-related system messages.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.chat_service import get_chat_service
from app.services.connection_manager import get_connection_manager


def _serialize_system_message(message) -> dict:
    """序列化系统消息，用于 WebSocket 广播。"""
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "sender_id": message.sender_id,
        "message_type": message.message_type.value,
        "content": None if message.recalled_at is not None else message.content,
        "created_at": message.created_at.isoformat(),
        "recalled_at": message.recalled_at.isoformat() if message.recalled_at is not None else None,
        "meta_json": message.meta_json,
        "sender": None,
    }


async def send_order_system_message(
    db: AsyncSession,
    order_id: int,
    content: str,
    meta_json: dict | None = None,
) -> None:
    """向订单关联的全部会话发送系统消息并广播。"""
    chat_service = get_chat_service(db)
    connection_manager = get_connection_manager()
    conversations = await chat_service.list_order_conversations(order_id)
    for conversation in conversations:
        system_message = await chat_service.send_system_message(
            conversation_id=conversation.id,
            content=content,
            meta_json=meta_json,
        )
        await connection_manager.send_to_conversation(
            conversation_id=conversation.id,
            data={
                "event": "new_message",
                "data": {
                    "conversation_id": conversation.id,
                    "message": _serialize_system_message(system_message),
                },
            },
        )
