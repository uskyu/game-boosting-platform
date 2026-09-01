"""
Chat schemas module.
Pydantic models for chat request and response validation.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.chat import ConversationType, MessageType
from app.models.order import OrderStatus
from app.models.user import UserRole


class ConversationCreateRequest(BaseModel):
    """Schema for creating or fetching a conversation."""

    target_user_id: int = Field(description="目标用户ID")
    order_id: int | None = Field(default=None, description="关联订单ID")


class MessageCreateRequest(BaseModel):
    """Schema for sending a text message."""

    content: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="消息内容",
    )


class ConversationReadRequest(BaseModel):
    """Schema for marking conversation messages as read."""

    last_read_message_id: int | None = Field(
        default=None,
        description="最后已读消息ID，不传则默认标记到最新消息",
    )


class ChatUserBrief(BaseModel):
    """Brief user info in chat responses."""

    id: int = Field(description="用户ID")
    username: str = Field(description="用户名")
    email: str = Field(description="邮箱地址")
    role: UserRole = Field(description="用户角色")
    avatar_url: str | None = Field(default=None, description="头像URL")

    model_config = ConfigDict(from_attributes=True)


class ConversationParticipantResponse(BaseModel):
    """Conversation participant response schema."""

    id: int = Field(description="参与者记录ID")
    user_id: int = Field(description="用户ID")
    role_snapshot: str = Field(description="加入时角色快照")
    joined_at: datetime = Field(description="加入时间")
    last_read_message_id: int | None = Field(default=None, description="最后已读消息ID")
    last_read_at: datetime | None = Field(default=None, description="最后已读时间")
    user: ChatUserBrief = Field(description="参与者信息")

    model_config = ConfigDict(from_attributes=True)


class ConversationOrderBrief(BaseModel):
    """Brief order info embedded in conversation responses."""

    id: int = Field(description="订单ID")
    user_id: int = Field(description="下单用户ID")
    booster_id: int | None = Field(default=None, description="接单代练ID")
    game_name: str = Field(description="游戏名称")
    current_rank: str = Field(description="当前段位")
    target_rank: str = Field(description="目标段位")
    price: Decimal = Field(description="订单价格")
    status: OrderStatus = Field(description="订单状态")

    model_config = ConfigDict(from_attributes=True)


class ConversationResponse(BaseModel):
    """Conversation detail response schema."""

    id: int = Field(description="会话ID")
    type: ConversationType = Field(description="会话类型")
    order_id: int | None = Field(default=None, description="关联订单ID")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")
    last_message_at: datetime | None = Field(default=None, description="最后消息时间")
    last_message_preview: str | None = Field(default=None, description="最后消息预览")
    unread_count: int = Field(default=0, description="当前用户未读数")
    participants: list[ConversationParticipantResponse] = Field(description="参与者列表")
    other_participants: list[ChatUserBrief] = Field(description="除当前用户之外的参与者")
    order: ConversationOrderBrief | None = Field(default=None, description="关联订单摘要")

    model_config = ConfigDict(from_attributes=True)


class ConversationListResponse(BaseModel):
    """Paginated conversation list response."""

    items: list[ConversationResponse] = Field(description="会话列表")
    total: int = Field(description="总数量")
    page: int = Field(description="当前页码")
    page_size: int = Field(description="每页数量")
    pages: int = Field(description="总页数")


class ChatMessageResponse(BaseModel):
    """Chat message response schema."""

    id: int = Field(description="消息ID")
    conversation_id: int = Field(description="会话ID")
    sender_id: int | None = Field(default=None, description="发送者ID")
    message_type: MessageType = Field(description="消息类型")
    content: str | None = Field(default=None, description="消息内容")
    created_at: datetime = Field(description="创建时间")
    recalled_at: datetime | None = Field(default=None, description="撤回时间")
    meta_json: dict[str, Any] | None = Field(default=None, description="附加元数据")
    sender: ChatUserBrief | None = Field(default=None, description="发送者信息")

    model_config = ConfigDict(from_attributes=True)


class InviteAdminResponse(BaseModel):
    """Invite admin response schema."""

    message: str = Field(description="操作结果消息")
    success: bool = Field(default=True, description="是否成功")
    admin: ChatUserBrief = Field(description="加入会话的管理员")


class UnreadSummaryResponse(BaseModel):
    """Unread summary response schema."""

    total_unread: int = Field(description="未读消息总数")
    conversations_with_unread: int = Field(description="含未读消息的会话数")

