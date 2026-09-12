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

# 单个慢客户端的发送超时：到点弃单关连接，绝不拖累其他接收者，
# 也不会把发起请求的事务拖在原地等一个卡死的套接字。
_SEND_TIMEOUT_SECONDS = 5.0


class ConnectionManager:
    """WebSocket 连接管理器。

    同一用户允许多条并存连接（多标签页/多端），新连接不再挤掉旧连接——
    旧实现会让开两个标签页的用户陷入「互踢-重连」死循环。
    """

    def __init__(self) -> None:
        self.connections: dict[int, list[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        """注册一条用户连接。

        调用方须已完成 ``websocket.accept()``；对已接受的套接字二次
        accept 会触发 ASGI 协议错误（Expected ... got 'websocket.accept'）。
        """
        async with self._lock:
            self.connections.setdefault(user_id, []).append(websocket)

    async def disconnect(self, user_id: int, websocket: WebSocket | None = None) -> None:
        """移除一条连接；websocket 为 None 时移除该用户全部连接。"""
        async with self._lock:
            if websocket is None:
                self.connections.pop(user_id, None)
                return
            sockets = self.connections.get(user_id)
            if not sockets:
                return
            with contextlib.suppress(ValueError):
                sockets.remove(websocket)
            if not sockets:
                self.connections.pop(user_id, None)

    async def _send_and_prune(self, user_id: int, websocket: WebSocket, data: dict) -> bool:
        """向单条连接发送；超时/失败即关闭并摘除该连接。"""
        try:
            await asyncio.wait_for(
                websocket.send_json(data), timeout=_SEND_TIMEOUT_SECONDS
            )
            return True
        except Exception:
            with contextlib.suppress(Exception):
                await websocket.close(code=1011, reason="send failed or timed out")
            await self.disconnect(user_id, websocket)
            return False

    async def send_to_user(self, user_id: int, data: dict) -> None:
        """向用户的全部在线连接并发发送。"""
        async with self._lock:
            sockets = list(self.connections.get(user_id, ()))
        if not sockets:
            return
        await asyncio.gather(
            *(self._send_and_prune(user_id, ws, data) for ws in sockets)
        )

    async def send_to_conversation(
        self,
        conversation_id: int,
        data: dict,
        exclude_user_id: int | None = None,
    ) -> None:
        """向会话中的在线参与者并发广播消息。"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(ConversationParticipant.user_id).where(
                    ConversationParticipant.conversation_id == conversation_id
                )
            )
            participant_ids = list(dict.fromkeys(result.scalars().all()))

        targets = [uid for uid in participant_ids if uid != exclude_user_id]
        if not targets:
            return

        async with self._lock:
            socket_pairs = [
                (uid, websocket)
                for uid in targets
                for websocket in list(self.connections.get(uid, ()))
            ]
        if not socket_pairs:
            return
        await asyncio.gather(
            *(self._send_and_prune(uid, ws, data) for uid, ws in socket_pairs)
        )

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
