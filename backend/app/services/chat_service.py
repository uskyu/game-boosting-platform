"""
Chat service module.
Handles conversation and message related business logic.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.config import settings
from app.core.security import escape_like
from app.models.chat import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Message,
    MessageDeletion,
    MessageType,
)
from app.models.order import Order
from app.models.user import User, UserRole


class ChatService:
    """聊天业务服务。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create_or_get_conversation(
        self,
        current_user: User,
        target_user_id: int,
        order_id: int | None = None,
    ) -> Conversation:
        """创建或获取会话。"""
        if current_user.id == target_user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="不能和自己发起对话",
            )

        target_user = await self._get_active_user(target_user_id)
        conversation_type = ConversationType.PRIVATE

        if order_id is not None:
            order = await self._get_order_for_conversation(order_id)

            order_participants = {order.user_id, order.booster_id} - {None}

            # PENDING 订单允许任意代练师联系客户（接单前沟通）
            current_is_participant = (
                current_user.id in order_participants
                or (order.status.value == "PENDING" and current_user.role.value == "booster")
            )
            if not current_is_participant:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="当前用户无权基于该订单创建对话",
                )

            target_is_participant = (
                target_user.id in order_participants
                or (order.status.value == "PENDING" and target_user.role.value == "booster")
            )
            if not target_is_participant:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="目标用户不属于该订单",
                )

            conversation_type = ConversationType.ORDER

        conversation = await self._find_existing_conversation(
            user_id=current_user.id,
            target_user_id=target_user.id,
            conversation_type=conversation_type,
            order_id=order_id,
        )
        if conversation is not None:
            conversation.unread_count = await self._get_unread_count(
                current_user=current_user,
                conversation_id=conversation.id,
            )
            return conversation

        # Serialize concurrent creations so we don't spawn duplicate
        # conversations between the same pair of users. Locks the two user
        # rows in ascending id order to avoid deadlock between symmetrical
        # calls (A→B and B→A).
        for uid in sorted({current_user.id, target_user.id}):
            await self._db.execute(
                select(User.id).where(User.id == uid).with_for_update()
            )

        # Re-check under the lock: another concurrent request may have
        # just created the conversation we were about to create.
        conversation = await self._find_existing_conversation(
            user_id=current_user.id,
            target_user_id=target_user.id,
            conversation_type=conversation_type,
            order_id=order_id,
        )
        if conversation is not None:
            conversation.unread_count = await self._get_unread_count(
                current_user=current_user,
                conversation_id=conversation.id,
            )
            return conversation

        conversation = Conversation(
            type=conversation_type,
            order_id=order_id,
        )
        self._db.add(conversation)
        try:
            await self._db.flush()
        except IntegrityError:
            # The uq_conversations_order_id constraint fired: another request
            # created the ORDER conversation between the locking window and
            # our insert (e.g. in a different DB session that committed).
            await self._db.rollback()
            existing = await self._find_existing_conversation(
                user_id=current_user.id,
                target_user_id=target_user.id,
                conversation_type=conversation_type,
                order_id=order_id,
            )
            if existing is None:
                raise
            existing.unread_count = await self._get_unread_count(
                current_user=current_user,
                conversation_id=existing.id,
            )
            return existing

        self._db.add_all(
            [
                ConversationParticipant(
                    conversation_id=conversation.id,
                    user_id=current_user.id,
                    role_snapshot=current_user.role.value,
                ),
                ConversationParticipant(
                    conversation_id=conversation.id,
                    user_id=target_user.id,
                    role_snapshot=target_user.role.value,
                ),
            ]
        )
        await self._db.flush()

        conversation = await self._get_conversation_or_404(conversation.id)
        conversation.unread_count = 0
        return conversation

    async def get_user_conversations(
        self,
        current_user: User,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Conversation], int]:
        """获取当前用户的会话列表。"""
        count_result = await self._db.execute(
            select(func.count(ConversationParticipant.id)).where(
                ConversationParticipant.user_id == current_user.id
            )
        )
        total = int(count_result.scalar() or 0)

        offset = (page - 1) * page_size
        result = await self._db.execute(
            select(ConversationParticipant)
            .options(
                joinedload(ConversationParticipant.conversation).joinedload(Conversation.order),
                joinedload(ConversationParticipant.conversation)
                .selectinload(Conversation.participants)
                .joinedload(ConversationParticipant.user),
            )
            .join(Conversation, Conversation.id == ConversationParticipant.conversation_id)
            .where(ConversationParticipant.user_id == current_user.id)
            .order_by(Conversation.last_message_at.desc(), Conversation.id.desc())
            .offset(offset)
            .limit(page_size)
        )
        participant_rows = list(result.scalars().unique().all())

        conversations = [row.conversation for row in participant_rows]
        unread_map = await self._get_unread_count_map(
            current_user=current_user,
            conversation_ids=[conversation.id for conversation in conversations],
        )
        for conversation in conversations:
            conversation.unread_count = unread_map.get(conversation.id, 0)

        return conversations, total

    async def get_conversation_with_access_check(
        self,
        conversation_id: int,
        current_user: User,
    ) -> Conversation:
        """获取会话详情并校验访问权限。"""
        await self._get_participant_or_403(
            conversation_id=conversation_id,
            user_id=current_user.id,
        )
        conversation = await self._get_conversation_or_404(conversation_id)
        conversation.unread_count = await self._get_unread_count(
            current_user=current_user,
            conversation_id=conversation_id,
        )
        return conversation

    async def list_messages(
        self,
        conversation_id: int,
        current_user: User,
        before_id: int | None = None,
        limit: int = 30,
    ) -> list[Message]:
        """获取消息历史。"""
        await self._get_participant_or_403(
            conversation_id=conversation_id,
            user_id=current_user.id,
        )

        query = select(Message).where(Message.conversation_id == conversation_id)

        if before_id is not None:
            query = query.where(Message.id < before_id)

        if current_user.role != UserRole.ADMIN:
            query = (
                query.outerjoin(
                    MessageDeletion,
                    and_(
                        MessageDeletion.message_id == Message.id,
                        MessageDeletion.user_id == current_user.id,
                    ),
                )
                .where(MessageDeletion.id.is_(None))
            )

        query = query.order_by(Message.id.desc()).limit(limit)
        result = await self._db.execute(query)
        messages = list(result.scalars().unique().all())
        messages.reverse()
        return messages

    async def search_messages(
        self,
        conversation_id: int,
        current_user: User,
        query_text: str,
        limit: int = 20,
    ) -> list[Message]:
        """在会话内搜索消息。"""
        await self._get_participant_or_403(
            conversation_id=conversation_id,
            user_id=current_user.id,
        )

        keyword = query_text.strip()
        if not keyword:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="搜索关键词不能为空",
            )

        query = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .where(Message.recalled_at.is_(None))
            .where(Message.content.like(f"%{escape_like(keyword)}%"))
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )

        if current_user.role != UserRole.ADMIN:
            query = (
                query.outerjoin(
                    MessageDeletion,
                    and_(
                        MessageDeletion.message_id == Message.id,
                        MessageDeletion.user_id == current_user.id,
                    ),
                )
                .where(MessageDeletion.id.is_(None))
            )

        result = await self._db.execute(query)
        return list(result.scalars().unique().all())

    async def send_message(
        self,
        conversation_id: int,
        current_user: User,
        content: str,
    ) -> Message:
        """发送文本消息。"""
        await self._get_participant_or_403(
            conversation_id=conversation_id,
            user_id=current_user.id,
        )

        clean_content = content.strip()
        if not clean_content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="消息内容不能为空",
            )

        if len(clean_content) > 2000:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="消息内容不能超过2000个字符",
            )

        now = datetime.now(timezone.utc)
        message = Message(
            conversation_id=conversation_id,
            sender_id=current_user.id,
            message_type=MessageType.TEXT,
            content=clean_content,
            created_at=now,
        )
        self._db.add(message)
        await self._db.flush()

        await self._update_conversation_last_message(
            conversation_id=conversation_id,
            message=message,
        )

        return await self._get_message_or_404(message.id)

    async def send_image_message(
        self,
        conversation_id: int,
        current_user: User,
        file: UploadFile,
    ) -> Message:
        """发送图片消息。"""
        await self._get_participant_or_403(
            conversation_id=conversation_id,
            user_id=current_user.id,
        )

        allowed_types = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
        }

        if not file.content_type or file.content_type not in allowed_types:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="仅支持上传 jpg、png、gif、webp 图片",
            )

        max_size = 5 * 1024 * 1024
        file_bytes = await file.read(max_size + 1)
        if not file_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="上传图片不能为空",
            )

        if len(file_bytes) > max_size:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="图片大小不能超过5MB",
            )

        upload_dir = Path(settings.UPLOAD_DIR) / "chat"
        upload_dir.mkdir(parents=True, exist_ok=True)

        suffix = allowed_types[file.content_type]
        file_name = f"{uuid4().hex}{suffix}"
        file_path = upload_dir / file_name
        file_path.write_bytes(file_bytes)

        now = datetime.now(timezone.utc)
        message = Message(
            conversation_id=conversation_id,
            sender_id=current_user.id,
            message_type=MessageType.IMAGE,
            content=f"/uploads/chat/{file_name}",
            created_at=now,
        )
        self._db.add(message)
        await self._db.flush()

        await self._update_conversation_last_message(
            conversation_id=conversation_id,
            message=message,
        )

        return await self._get_message_or_404(message.id)

    async def send_system_message(
        self,
        conversation_id: int,
        content: str,
        meta_json: dict | None = None,
    ) -> Message:
        """发送系统消息。"""
        conversation = await self._get_conversation_or_404(conversation_id)
        now = datetime.now(timezone.utc)
        message = Message(
            conversation_id=conversation.id,
            sender_id=None,
            message_type=MessageType.SYSTEM,
            content=content.strip(),
            meta_json=meta_json,
            created_at=now,
        )
        self._db.add(message)
        await self._db.flush()

        await self._update_conversation_last_message(
            conversation_id=conversation.id,
            message=message,
        )

        return await self._get_message_or_404(message.id)

    async def recall_message(
        self,
        message_id: int,
        current_user: User,
    ) -> Message:
        """撤回消息。"""
        message = await self._get_message_with_access_check(
            message_id=message_id,
            current_user=current_user,
        )

        if message.sender_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只能撤回自己发送的消息",
            )

        if message.message_type == MessageType.SYSTEM:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="系统消息不支持撤回",
            )

        if message.recalled_at is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该消息已撤回",
            )

        now = datetime.now(timezone.utc)
        created_at = message.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if now - created_at > timedelta(minutes=2):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="消息发送超过2分钟，无法撤回",
            )

        message.recalled_at = now
        await self._db.flush()
        await self._sync_conversation_last_message(message.conversation_id)
        return message

    async def delete_message_for_user(
        self,
        message_id: int,
        current_user: User,
    ) -> None:
        """为当前用户软删除消息。"""
        message = await self._get_message_with_access_check(
            message_id=message_id,
            current_user=current_user,
        )

        existing_result = await self._db.execute(
            select(MessageDeletion).where(
                MessageDeletion.message_id == message.id,
                MessageDeletion.user_id == current_user.id,
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            return

        deletion = MessageDeletion(
            message_id=message.id,
            user_id=current_user.id,
        )
        self._db.add(deletion)
        await self._db.flush()

    async def mark_read(
        self,
        conversation_id: int,
        current_user: User,
        last_read_message_id: int | None = None,
    ) -> int | None:
        """标记会话已读。"""
        participant = await self._get_participant_or_403(
            conversation_id=conversation_id,
            user_id=current_user.id,
        )

        resolved_message_id = last_read_message_id
        if resolved_message_id is None:
            latest_result = await self._db.execute(
                select(Message.id)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id.desc())
                .limit(1)
            )
            resolved_message_id = latest_result.scalar_one_or_none()
        else:
            message_result = await self._db.execute(
                select(Message.id).where(
                    Message.id == resolved_message_id,
                    Message.conversation_id == conversation_id,
                )
            )
            existing_message_id = message_result.scalar_one_or_none()
            if existing_message_id is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="最后已读消息不存在或不属于该会话",
                )

        if (
            participant.last_read_message_id is not None
            and resolved_message_id is not None
            and resolved_message_id < participant.last_read_message_id
        ):
            resolved_message_id = participant.last_read_message_id

        participant.last_read_message_id = resolved_message_id
        participant.last_read_at = datetime.now(timezone.utc)
        await self._db.flush()

        return resolved_message_id

    async def get_unread_summary(self, current_user: User) -> dict[str, int]:
        """获取未读汇总。"""
        participant_result = await self._db.execute(
            select(ConversationParticipant.conversation_id).where(
                ConversationParticipant.user_id == current_user.id
            )
        )
        conversation_ids = list(participant_result.scalars().all())
        unread_map = await self._get_unread_count_map(
            current_user=current_user,
            conversation_ids=conversation_ids,
        )

        total_unread = sum(unread_map.values())
        conversations_with_unread = sum(1 for count in unread_map.values() if count > 0)
        return {
            "total_unread": total_unread,
            "conversations_with_unread": conversations_with_unread,
        }

    async def invite_admin(
        self,
        conversation_id: int,
        current_user: User,
    ) -> User:
        """邀请管理员加入会话。"""
        await self._get_participant_or_403(
            conversation_id=conversation_id,
            user_id=current_user.id,
        )

        if current_user.role == UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="管理员无需再次邀请管理员介入",
            )

        conversation = await self._get_conversation_or_404(conversation_id)
        admin_exists = any(
            participant.role_snapshot == UserRole.ADMIN.value
            or participant.user.role == UserRole.ADMIN
            for participant in conversation.participants
        )
        if admin_exists:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该会话已有管理员介入",
            )

        admin_result = await self._db.execute(
            select(User)
            .where(User.role == UserRole.ADMIN, User.is_active.is_(True))
            .order_by(User.id.asc())
            .limit(1)
        )
        admin = admin_result.scalar_one_or_none()
        if admin is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="当前没有可用的管理员",
            )

        participant = ConversationParticipant(
            conversation_id=conversation.id,
            user_id=admin.id,
            role_snapshot=admin.role.value,
        )
        self._db.add(participant)
        await self._db.flush()

        return admin

    async def list_order_conversations(self, order_id: int) -> list[Conversation]:
        """获取某个订单关联的全部会话。"""
        result = await self._db.execute(
            select(Conversation)
            .options(
                joinedload(Conversation.order),
                selectinload(Conversation.participants).joinedload(ConversationParticipant.user),
            )
            .where(Conversation.order_id == order_id)
        )
        return list(result.scalars().unique().all())

    async def _get_active_user(self, user_id: int) -> User:
        result = await self._db.execute(
            select(User).where(
                User.id == user_id,
                User.is_active.is_(True),
            )
        )
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="目标用户不存在或已被禁用",
            )
        return user

    async def _get_order_for_conversation(self, order_id: int) -> Order:
        result = await self._db.execute(
            select(Order).where(Order.id == order_id)
        )
        order = result.scalar_one_or_none()
        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="订单不存在",
            )
        return order

    async def _find_existing_conversation(
        self,
        user_id: int,
        target_user_id: int,
        conversation_type: ConversationType,
        order_id: int | None,
    ) -> Conversation | None:
        query = (
            select(Conversation.id)
            .join(
                ConversationParticipant,
                ConversationParticipant.conversation_id == Conversation.id,
            )
            .where(Conversation.type == conversation_type)
            .where(ConversationParticipant.user_id.in_([user_id, target_user_id]))
            .group_by(Conversation.id)
            .having(func.count(func.distinct(ConversationParticipant.user_id)) == 2)
            .order_by(Conversation.id.desc())
        )

        if order_id is None:
            query = query.where(Conversation.order_id.is_(None))
        else:
            query = query.where(Conversation.order_id == order_id)

        result = await self._db.execute(query)
        conversation_id = result.scalars().first()
        if conversation_id is None:
            return None
        return await self._get_conversation_or_404(conversation_id)

    async def _get_conversation_or_404(self, conversation_id: int) -> Conversation:
        result = await self._db.execute(
            select(Conversation)
            .options(
                joinedload(Conversation.order),
                selectinload(Conversation.participants).joinedload(ConversationParticipant.user),
            )
            .where(Conversation.id == conversation_id)
        )
        conversation = result.scalars().unique().one_or_none()
        if conversation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="会话不存在",
            )
        return conversation

    async def _get_participant_or_403(
        self,
        conversation_id: int,
        user_id: int,
    ) -> ConversationParticipant:
        result = await self._db.execute(
            select(ConversationParticipant)
            .where(
                ConversationParticipant.conversation_id == conversation_id,
                ConversationParticipant.user_id == user_id,
            )
        )
        participant = result.scalar_one_or_none()
        if participant is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="无权访问该会话",
            )
        return participant

    async def _get_message_or_404(self, message_id: int) -> Message:
        result = await self._db.execute(
            select(Message).where(Message.id == message_id)
        )
        message = result.scalars().unique().one_or_none()
        if message is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="消息不存在",
            )
        return message

    async def _get_message_with_access_check(
        self,
        message_id: int,
        current_user: User,
    ) -> Message:
        message = await self._get_message_or_404(message_id)
        await self._get_participant_or_403(
            conversation_id=message.conversation_id,
            user_id=current_user.id,
        )
        return message

    async def _update_conversation_last_message(
        self,
        conversation_id: int,
        message: Message,
    ) -> None:
        conversation = await self._get_conversation_or_404(conversation_id)
        conversation.last_message_at = message.created_at
        conversation.last_message_preview = self._build_message_preview(message)
        await self._db.flush()

    async def _sync_conversation_last_message(self, conversation_id: int) -> None:
        conversation = await self._get_conversation_or_404(conversation_id)
        result = await self._db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(1)
        )
        latest_message = result.scalars().unique().one_or_none()

        if latest_message is None:
            conversation.last_message_at = None
            conversation.last_message_preview = None
        else:
            conversation.last_message_at = latest_message.created_at
            conversation.last_message_preview = self._build_message_preview(latest_message)

        await self._db.flush()

    def _build_message_preview(self, message: Message) -> str:
        if message.recalled_at is not None:
            return "消息已撤回"
        if message.message_type == MessageType.IMAGE:
            return "[图片]"
        return message.content[:100]

    async def _get_unread_count_map(
        self,
        current_user: User,
        conversation_ids: list[int],
    ) -> dict[int, int]:
        if not conversation_ids:
            return {}

        query = (
            select(
                Message.conversation_id,
                func.count(Message.id),
            )
            .join(
                ConversationParticipant,
                and_(
                    ConversationParticipant.conversation_id == Message.conversation_id,
                    ConversationParticipant.user_id == current_user.id,
                ),
            )
            .where(Message.conversation_id.in_(conversation_ids))
            .where(or_(Message.sender_id.is_(None), Message.sender_id != current_user.id))
            .where(Message.recalled_at.is_(None))
            .where(
                or_(
                    ConversationParticipant.last_read_message_id.is_(None),
                    Message.id > ConversationParticipant.last_read_message_id,
                )
            )
        )

        if current_user.role != UserRole.ADMIN:
            query = (
                query.outerjoin(
                    MessageDeletion,
                    and_(
                        MessageDeletion.message_id == Message.id,
                        MessageDeletion.user_id == current_user.id,
                    ),
                )
                .where(MessageDeletion.id.is_(None))
            )

        query = query.group_by(Message.conversation_id)

        result = await self._db.execute(query)
        return {
            conversation_id: int(count)
            for conversation_id, count in result.all()
        }

    async def _get_unread_count(
        self,
        current_user: User,
        conversation_id: int,
    ) -> int:
        unread_map = await self._get_unread_count_map(
            current_user=current_user,
            conversation_ids=[conversation_id],
        )
        return unread_map.get(conversation_id, 0)


def get_chat_service(db: AsyncSession) -> ChatService:
    """创建聊天服务实例。"""
    return ChatService(db)
