"""Chat API endpoints."""

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, File, Query, UploadFile, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DatabaseSession
from app.core.security import verify_token
from app.db.session import async_session_factory
from app.models.chat import Conversation, ConversationParticipant, Message
from app.models.user import User
from app.schemas.chat import (
    ChatMessageResponse,
    ChatUserBrief,
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationOrderBrief,
    ConversationParticipantResponse,
    ConversationReadRequest,
    ConversationResponse,
    InviteAdminResponse,
    MessageCreateRequest,
    UnreadSummaryResponse,
)
from app.schemas.user import MessageResponse
from app.services.chat_service import get_chat_service
from app.services.connection_manager import get_connection_manager

router = APIRouter(prefix="/chat", tags=["聊天"])


def _serialize_participant(
    participant: ConversationParticipant,
) -> ConversationParticipantResponse:
    return ConversationParticipantResponse(
        id=participant.id,
        user_id=participant.user_id,
        role_snapshot=participant.role_snapshot,
        joined_at=participant.joined_at,
        last_read_message_id=participant.last_read_message_id,
        last_read_at=participant.last_read_at,
        user=ChatUserBrief.model_validate(participant.user),
    )


def _serialize_conversation(
    conversation: Conversation,
    current_user_id: int,
) -> ConversationResponse:
    participants = sorted(
        conversation.participants,
        key=lambda item: item.id,
    )
    other_participants = [
        ChatUserBrief.model_validate(participant.user)
        for participant in participants
        if participant.user_id != current_user_id
    ]

    return ConversationResponse(
        id=conversation.id,
        type=conversation.type,
        order_id=conversation.order_id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        last_message_at=conversation.last_message_at,
        last_message_preview=conversation.last_message_preview,
        unread_count=int(getattr(conversation, "unread_count", 0) or 0),
        participants=[_serialize_participant(participant) for participant in participants],
        other_participants=other_participants,
        order=ConversationOrderBrief.model_validate(conversation.order)
        if conversation.order is not None
        else None,
    )


def _serialize_message(message: Message) -> ChatMessageResponse:
    return ChatMessageResponse(
        id=message.id,
        conversation_id=message.conversation_id,
        sender_id=message.sender_id,
        message_type=message.message_type,
        content=None if message.recalled_at is not None else message.content,
        created_at=message.created_at,
        recalled_at=message.recalled_at,
        meta_json=message.meta_json,
        sender=ChatUserBrief.model_validate(message.sender)
        if message.sender is not None
        else None,
    )


def _build_ws_event(event: str, data: dict[str, Any]) -> dict[str, Any]:
    """构造统一的 WebSocket 事件格式。"""
    return {
        "event": event,
        "data": data,
    }


async def _broadcast_new_message(
    message: Message,
    exclude_user_id: int | None = None,
) -> None:
    """广播新消息事件。"""
    await get_connection_manager().send_to_conversation(
        conversation_id=message.conversation_id,
        data=_build_ws_event(
            "new_message",
            {
                "conversation_id": message.conversation_id,
                "message": _serialize_message(message).model_dump(mode="json"),
            },
        ),
        exclude_user_id=exclude_user_id,
    )


async def _get_websocket_user(token: str | None) -> User | None:
    """通过 JWT 获取 WebSocket 当前用户。"""
    if token is None:
        return None

    payload = verify_token(token, token_type="access")
    if payload is None:
        return None

    user_id_str = payload.get("sub")
    if user_id_str is None:
        return None

    try:
        user_id = int(user_id_str)
    except (TypeError, ValueError):
        return None

    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(
                User.id == user_id,
                User.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()


async def _check_conversation_access(user_id: int, conversation_id: int) -> bool:
    """校验用户是否为会话参与者。"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(ConversationParticipant.id).where(
                ConversationParticipant.conversation_id == conversation_id,
                ConversationParticipant.user_id == user_id,
            )
        )
        return result.scalar_one_or_none() is not None


@router.websocket("/ws")
async def chat_websocket(websocket: WebSocket) -> None:
    """聊天 WebSocket 端点，只处理推送和 typing 转发。

    认证方式：连接建立后客户端必须在 5 秒内发送一条 ``auth`` 事件，
    携带 JWT access token。服务器验证通过后才开始接受后续消息。
    这避免了把 token 放在 query string 里被日志和代理记录。
    """
    await websocket.accept()

    # ── Phase 1: authenticate via first message ──
    try:
        auth_payload = await asyncio.wait_for(
            websocket.receive_json(),
            timeout=5,
        )
    except (asyncio.TimeoutError, WebSocketDisconnect, ValueError):
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="认证超时或格式无效",
        )
        return

    if not isinstance(auth_payload, dict) or auth_payload.get("event") != "auth":
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="首条消息必须为 auth 事件",
        )
        return

    token = (auth_payload.get("data") or {}).get("token")
    current_user = await _get_websocket_user(token)

    if current_user is None:
        await websocket.send_json(
            _build_ws_event("auth_fail", {"message": "认证失败"})
        )
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="认证失败",
        )
        return

    await websocket.send_json(
        _build_ws_event("auth_ok", {"user_id": current_user.id})
    )

    # ── Phase 2: normal message loop ──
    connection_manager = get_connection_manager()
    await connection_manager.connect(current_user.id, websocket)

    try:
        while True:
            try:
                payload = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=60,
                )
            except asyncio.TimeoutError:
                await websocket.close(code=1000, reason="心跳超时")
                break
            except WebSocketDisconnect:
                break
            except ValueError:
                await websocket.send_json(
                    _build_ws_event("error", {"message": "消息格式无效"})
                )
                continue

            if not isinstance(payload, dict):
                await websocket.send_json(
                    _build_ws_event("error", {"message": "消息格式无效"})
                )
                continue

            event = payload.get("event")
            data = payload.get("data") or {}

            if event == "ping":
                await websocket.send_json(_build_ws_event("pong", {}))
                continue

            if event != "typing":
                await websocket.send_json(
                    _build_ws_event("error", {"message": "不支持的事件类型"})
                )
                continue

            if not isinstance(data, dict):
                await websocket.send_json(
                    _build_ws_event("error", {"message": "typing 事件参数无效"})
                )
                continue

            conversation_id = data.get("conversation_id")
            if not isinstance(conversation_id, int):
                await websocket.send_json(
                    _build_ws_event("error", {"message": "conversation_id 无效"})
                )
                continue

            has_access = await _check_conversation_access(
                user_id=current_user.id,
                conversation_id=conversation_id,
            )
            if not has_access:
                await websocket.send_json(
                    _build_ws_event("error", {"message": "无权访问该会话"})
                )
                continue

            await connection_manager.send_to_conversation(
                conversation_id=conversation_id,
                data=_build_ws_event(
                    "typing",
                    {
                        "conversation_id": conversation_id,
                        "user_id": current_user.id,
                    },
                ),
                exclude_user_id=current_user.id,
            )
    finally:
        await connection_manager.disconnect(current_user.id, websocket)


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    summary="创建或获取会话",
    description="按目标用户和可选订单ID创建或获取现有会话",
)
async def create_or_get_conversation(
    payload: ConversationCreateRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> ConversationResponse:
    chat_service = get_chat_service(db)
    conversation = await chat_service.create_or_get_conversation(
        current_user=current_user,
        target_user_id=payload.target_user_id,
        order_id=payload.order_id,
    )
    return _serialize_conversation(conversation, current_user.id)


@router.get(
    "/conversations",
    response_model=ConversationListResponse,
    summary="获取会话列表",
    description="获取当前用户参与的会话列表，按最后消息时间倒序",
)
async def list_conversations(
    current_user: CurrentUser,
    db: DatabaseSession,
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 20,
) -> ConversationListResponse:
    chat_service = get_chat_service(db)
    conversations, total = await chat_service.get_user_conversations(
        current_user=current_user,
        page=page,
        page_size=page_size,
    )
    pages = (total + page_size - 1) // page_size if total > 0 else 0

    return ConversationListResponse(
        items=[
            _serialize_conversation(conversation, current_user.id)
            for conversation in conversations
        ],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    summary="获取会话详情",
    description="获取指定会话详情，仅参与者可访问",
)
async def get_conversation(
    conversation_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> ConversationResponse:
    chat_service = get_chat_service(db)
    conversation = await chat_service.get_conversation_with_access_check(
        conversation_id=conversation_id,
        current_user=current_user,
    )
    return _serialize_conversation(conversation, current_user.id)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[ChatMessageResponse],
    summary="获取消息历史",
    description="获取会话消息历史，支持基于消息ID的向上翻页",
)
async def list_messages(
    conversation_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
    before_id: Annotated[
        int | None,
        Query(description="获取该消息ID之前的历史消息"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="返回数量")] = 30,
) -> list[ChatMessageResponse]:
    chat_service = get_chat_service(db)
    messages = await chat_service.list_messages(
        conversation_id=conversation_id,
        current_user=current_user,
        before_id=before_id,
        limit=limit,
    )
    return [_serialize_message(message) for message in messages]


@router.get(
    "/conversations/{conversation_id}/messages/search",
    response_model=list[ChatMessageResponse],
    summary="搜索会话消息",
    description="在指定会话中按关键词搜索消息内容",
)
async def search_messages(
    conversation_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
    q: Annotated[str, Query(min_length=1, max_length=200, description="搜索关键词")],
    limit: Annotated[int, Query(ge=1, le=100, description="返回数量")] = 20,
) -> list[ChatMessageResponse]:
    chat_service = get_chat_service(db)
    messages = await chat_service.search_messages(
        conversation_id=conversation_id,
        current_user=current_user,
        query_text=q,
        limit=limit,
    )
    return [_serialize_message(message) for message in messages]


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ChatMessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="发送文本消息",
    description="向指定会话发送文本消息",
)
async def send_message(
    conversation_id: int,
    payload: MessageCreateRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> ChatMessageResponse:
    chat_service = get_chat_service(db)
    message = await chat_service.send_message(
        conversation_id=conversation_id,
        current_user=current_user,
        content=payload.content,
    )
    await _broadcast_new_message(
        message=message,
        exclude_user_id=current_user.id,
    )
    return _serialize_message(message)


@router.post(
    "/conversations/{conversation_id}/upload",
    response_model=ChatMessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="发送图片消息",
    description="上传图片并发送为图片消息",
)
async def upload_image(
    conversation_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
    file: UploadFile = File(...),
) -> ChatMessageResponse:
    chat_service = get_chat_service(db)
    message = await chat_service.send_image_message(
        conversation_id=conversation_id,
        current_user=current_user,
        file=file,
    )
    await _broadcast_new_message(
        message=message,
        exclude_user_id=current_user.id,
    )
    return _serialize_message(message)


@router.post(
    "/conversations/{conversation_id}/read",
    response_model=MessageResponse,
    summary="标记会话已读",
    description="将会话标记为已读，可指定最后已读消息ID",
)
async def mark_conversation_read(
    conversation_id: int,
    payload: ConversationReadRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> MessageResponse:
    chat_service = get_chat_service(db)
    last_read_message_id = await chat_service.mark_read(
        conversation_id=conversation_id,
        current_user=current_user,
        last_read_message_id=payload.last_read_message_id,
    )
    await get_connection_manager().send_to_conversation(
        conversation_id=conversation_id,
        data=_build_ws_event(
            "message_read",
            {
                "conversation_id": conversation_id,
                "user_id": current_user.id,
                "last_read_message_id": last_read_message_id,
            },
        ),
        exclude_user_id=current_user.id,
    )
    return MessageResponse(message="已更新会话已读状态", success=True)


@router.post(
    "/messages/{message_id}/recall",
    response_model=MessageResponse,
    summary="撤回消息",
    description="撤回当前用户在2分钟内发送的消息",
)
async def recall_message(
    message_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> MessageResponse:
    chat_service = get_chat_service(db)
    message = await chat_service.recall_message(
        message_id=message_id,
        current_user=current_user,
    )
    await get_connection_manager().send_to_conversation(
        conversation_id=message.conversation_id,
        data=_build_ws_event(
            "message_recalled",
            {
                "conversation_id": message.conversation_id,
                "message_id": message.id,
                "recalled_by": current_user.id,
            },
        ),
        exclude_user_id=current_user.id,
    )
    return MessageResponse(message="消息已撤回", success=True)


@router.delete(
    "/messages/{message_id}",
    response_model=MessageResponse,
    summary="删除消息",
    description="仅对当前用户隐藏指定消息",
)
async def delete_message(
    message_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> MessageResponse:
    chat_service = get_chat_service(db)
    await chat_service.delete_message_for_user(
        message_id=message_id,
        current_user=current_user,
    )
    return MessageResponse(message="消息已删除", success=True)


@router.post(
    "/conversations/{conversation_id}/invite-admin",
    response_model=InviteAdminResponse,
    summary="邀请管理员介入",
    description="邀请管理员加入当前会话并发送系统消息",
)
async def invite_admin(
    conversation_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> InviteAdminResponse:
    chat_service = get_chat_service(db)
    connection_manager = get_connection_manager()
    admin = await chat_service.invite_admin(
        conversation_id=conversation_id,
        current_user=current_user,
    )
    system_message = await chat_service.send_system_message(
        conversation_id=conversation_id,
        content=f"{current_user.username} 请求客服介入",
        meta_json={
            "action": "invite_admin",
            "requesting_user_id": current_user.id,
            "admin_id": admin.id,
        },
    )
    admin_joined_event = _build_ws_event(
        "admin_joined",
        {
            "conversation_id": conversation_id,
            "admin": ChatUserBrief.model_validate(admin).model_dump(mode="json"),
        },
    )
    # send_to_conversation 已包含管理员（刚被添加为参与者），无需单独推送
    await connection_manager.send_to_conversation(
        conversation_id=conversation_id,
        data=admin_joined_event,
    )
    await _broadcast_new_message(system_message)
    return InviteAdminResponse(
        message="管理员已加入会话",
        success=True,
        admin=ChatUserBrief.model_validate(admin),
    )


@router.get(
    "/unread-summary",
    response_model=UnreadSummaryResponse,
    summary="获取未读汇总",
    description="获取当前用户所有会话的未读消息汇总",
)
async def get_unread_summary(
    current_user: CurrentUser,
    db: DatabaseSession,
) -> UnreadSummaryResponse:
    chat_service = get_chat_service(db)
    summary = await chat_service.get_unread_summary(current_user=current_user)
    return UnreadSummaryResponse(**summary)
