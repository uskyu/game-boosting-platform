"""
Connection manager module.
Handles WebSocket connection lifecycle and message broadcasting.
"""

import asyncio
import contextlib

from fastapi import WebSocket
from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.chat import ConversationParticipant


class ConnectionManager:
    """WebSocket 连接管理器。"""

    def __init__(self) -> None:
        self.connections: dict[int, WebSocket] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        """建立用户连接，同一用户只保留一个活动连接。"""
        await websocket.accept()

        async with self._lock:
            previous = self.connections.get(user_id)
            self.connections[user_id] = websocket

        if previous is not None and previous is not websocket:
            with contextlib.suppress(Exception):
                await previous.close(code=1000, reason="新连接已建立")

    async def disconnect(self, user_id: int, websocket: WebSocket | None = None) -> None:
        """断开用户连接。"""
        async with self._lock:
            current = self.connections.get(user_id)
            if current is None:
                return
            if websocket is not None and current is not websocket:
                return
            self.connections.pop(user_id, None)

    async def send_to_user(self, user_id: int, data: dict) -> None:
        """向单个在线用户发送消息。"""
        async with self._lock:
            websocket = self.connections.get(user_id)

        if websocket is None:
            return

        try:
            await websocket.send_json(data)
        except Exception:
            await self.disconnect(user_id, websocket)

    async def send_to_conversation(
        self,
        conversation_id: int,
        data: dict,
        exclude_user_id: int | None = None,
    ) -> None:
        """向会话中的在线参与者广播消息。"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(ConversationParticipant.user_id).where(
                    ConversationParticipant.conversation_id == conversation_id
                )
            )
            participant_ids = list(dict.fromkeys(result.scalars().all()))

        for user_id in participant_ids:
            if exclude_user_id is not None and user_id == exclude_user_id:
                continue
            await self.send_to_user(user_id, data)


    async def send_notification(self, user_id: int, notification: dict) -> None:
        """向用户推送实时通知事件。"""
        await self.send_to_user(user_id, {
            "event": "notification",
            "data": notification,
        })


connection_manager = ConnectionManager()


def get_connection_manager() -> ConnectionManager:
    """获取全局连接管理器单例。"""
    return connection_manager
