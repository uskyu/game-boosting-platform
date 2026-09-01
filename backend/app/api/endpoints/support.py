"""AI customer support endpoints."""

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DatabaseSession
from app.services.support_service import get_support_service

router = APIRouter(prefix="/support", tags=["客服"])


class SupportMessageRequest(BaseModel):
    """Request body for sending a message to AI support."""

    message: str = Field(..., min_length=1, max_length=500, description="用户消息")
    template_key: str | None = Field(default=None, description="模板快捷键")


class SupportAction(BaseModel):
    """A suggested action button."""

    label: str = Field(description="按钮文字")
    type: str = Field(description="操作类型: navigate/transfer/action")
    link: str = Field(default="", description="跳转链接")


class SupportResponse(BaseModel):
    """AI support response."""

    reply: str = Field(description="回复内容")
    category: str = Field(description="问题分类")
    actions: list[SupportAction] = Field(default_factory=list, description="建议操作")
    need_human: bool = Field(default=False, description="是否建议转人工")


class TemplateCategory(BaseModel):
    """Template category with quick replies."""

    key: str
    label: str
    icon: str
    templates: list[dict[str, str]]


@router.get("/templates", response_model=list[TemplateCategory])
async def get_templates(
    current_user: CurrentUser,
) -> list[dict[str, Any]]:
    """获取客服模板分类和快捷回复选项。"""
    svc = get_support_service()
    return svc.get_templates()


@router.post("/chat", response_model=SupportResponse)
async def chat_with_support(
    body: SupportMessageRequest,
    current_user: CurrentUser,
) -> SupportResponse:
    """发送消息给AI客服，获取回复。"""
    svc = get_support_service()

    # Try template response first (instant, no API call)
    if body.template_key:
        template_resp = svc.get_template_response(body.template_key)
        if template_resp:
            return SupportResponse(**template_resp)

    # Fall back to AI response
    result = await svc.get_ai_response(body.message)
    return SupportResponse(**result)


@router.post("/transfer-human")
async def transfer_to_human(
    current_user: CurrentUser,
    db: DatabaseSession,
) -> dict[str, Any]:
    """转接人工客服 - 创建与管理员的会话。"""
    from sqlalchemy import select

    from app.models.user import User, UserRole
    from app.services.chat_service import get_chat_service

    # Find an available admin
    result = await db.execute(
        select(User)
        .where(User.role == UserRole.ADMIN, User.is_active.is_(True))
        .order_by(User.id.asc())
        .limit(1)
    )
    admin = result.scalar_one_or_none()
    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="暂无可用的人工客服",
        )

    chat_service = get_chat_service(db)
    conversation = await chat_service.create_or_get_conversation(
        current_user=current_user,
        target_user_id=admin.id,
    )

    # Send a system message indicating transfer from AI
    await chat_service.send_system_message(
        conversation_id=conversation.id,
        content="用户从AI客服转接到人工客服",
        meta_json={"event": "ai_transfer", "user_id": current_user.id},
    )
    await db.commit()

    return {
        "conversation_id": conversation.id,
        "admin_username": admin.username,
        "message": "已转接人工客服",
    }
