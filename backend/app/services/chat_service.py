"""
Chat service module.
Handles conversation and message related business logic.
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, joinedload, selectinload

from app.api.rate_limit import enforce_user_limit
from app.core.config import settings
from app.core.security import escape_like
from app.core.title_rules import apply_title_replacements
from app.models.chat import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Message,
    MessageDeletion,
    MessageType,
)
from app.models.order import ClaimStatus, Order, OrderClaim, OrderStatus
from app.models.user import User, UserRole
from app.services.site_setting_service import get_order_title_replacements

logger = logging.getLogger(__name__)

# 严格限流只作用于「即将新建 ORDER 会话」的路径（每用户 20 次/10 分钟）；
# 已有会话查询命中时直接返回，不消耗额度。端点层的宽限流由 API 层负责。
_NEW_ORDER_CONVERSATION_RATE = (20, 600)
_NEW_ORDER_CONVERSATION_SCOPE = "chat_order_conversation_create"


def _is_order_conversation_unique_conflict(exc: IntegrityError) -> bool:
    """是否命中了订单会话唯一键（旧「每单一条」或新「每单+对方」）。

    MySQL/InnoDB 的重复键错误码固定为 1062，消息里带约束名。这里只接受
    目标约束，避免把外键失败、参与者唯一键等其他数据库错误误当成并发
    建联而吞掉。
    """
    orig = exc.orig
    args = getattr(orig, "args", ()) if orig is not None else ()
    if not args or args[0] != 1062:
        return False
    message = str(orig)
    return (
        "uq_conversations_order_id" in message
        or "uq_conversations_order_counterpart" in message
    )


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
        # 先取成普通整数：唯一键冲突分支可能要回滚事务，回滚会让 ORM 对象
        # 过期，之后再读 current_user.id 会触发异步隐式加载（MissingGreenlet）。
        current_user_id = current_user.id
        if current_user_id == target_user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="不能和自己发起对话",
            )

        conversation_type = ConversationType.PRIVATE
        order: Order | None = None
        counterpart_user_id: int | None = None

        if order_id is not None:
            order = await self._get_order_for_conversation(order_id)
            conversation_type = ConversationType.ORDER
            counterpart_user_id = self._compute_counterpart_id(
                order=order,
                user_id=current_user_id,
                target_user_id=target_user_id,
            )

            # 已有会话优先：订单进行期聊起来的会话必须能随时打开，包含订单
            # 完结后的售后回看，以及对 can_accept=false 账号的历史售后。
            # 命中就跳过目标账号活跃状态与新建会话资格校验（历史会话本身
            # 已经证明双方当时建联成功）。
            existing_conversation = await self._find_existing_conversation(
                user_id=current_user_id,
                target_user_id=target_user_id,
                conversation_type=conversation_type,
                order_id=order_id,
                counterpart_user_id=counterpart_user_id,
            )
            if existing_conversation is not None:
                existing_conversation.unread_count = await self._get_unread_count(
                    current_user=current_user,
                    conversation_id=existing_conversation.id,
                )
                return existing_conversation

        target_user = await self._get_active_user(target_user_id)

        if order is not None:
            # 走到这里说明要新建会话，才做可联络与参与者资格校验（终态
            # 订单只校验归档，联络资格由下面的参与者集合判定）。
            self._assert_order_contactable(order)
            order_participants = await self._get_order_participant_ids(order)
            self._assert_order_conversation_eligible(
                order=order,
                current_user=current_user,
                target_user=target_user,
                participant_ids=order_participants,
            )
            if counterpart_user_id is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="只能由订单发布人与服务方发起对话",
                )

            existing_conversation = await self._find_existing_conversation(
                user_id=current_user_id,
                target_user_id=target_user.id,
                conversation_type=conversation_type,
                order_id=order_id,
                counterpart_user_id=counterpart_user_id,
            )
            if existing_conversation is not None:
                existing_conversation.unread_count = await self._get_unread_count(
                    current_user=current_user,
                    conversation_id=existing_conversation.id,
                )
                return existing_conversation
        else:
            existing_conversation = await self._find_existing_conversation(
                user_id=current_user_id,
                target_user_id=target_user.id,
                conversation_type=conversation_type,
                order_id=order_id,
            )
            if existing_conversation is not None:
                existing_conversation.unread_count = await self._get_unread_count(
                    current_user=current_user,
                    conversation_id=existing_conversation.id,
                )
                return existing_conversation

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
            user_id=current_user_id,
            target_user_id=target_user.id,
            conversation_type=conversation_type,
            order_id=order_id,
            counterpart_user_id=counterpart_user_id,
        )
        if conversation is not None:
            conversation.unread_count = await self._get_unread_count(
                current_user=current_user,
                conversation_id=conversation.id,
            )
            return conversation

        # 严格限流只覆盖「确实要新建 ORDER 会话」的路径：上面的 existing
        # 查询已全部未命中，不会误伤历史会话的打开/续聊。
        if order is not None:
            enforce_user_limit(
                current_user_id,
                _NEW_ORDER_CONVERSATION_SCOPE,
                _NEW_ORDER_CONVERSATION_RATE[0],
                _NEW_ORDER_CONVERSATION_RATE[1],
                code="chat_rate_limited",
            )

        # 先把调用方此前排队的写入落库（例如服务卡下单刚创建的订单）。
        # SQLAlchemy 在 begin_nested() 建立 SAVEPOINT 之前会先 flush 待处理
        # 状态；不先 flush，这些写入会落在保存点之外，语义不可控。
        await self._db.flush()

        conversation = Conversation(
            type=conversation_type,
            order_id=order_id,
            counterpart_user_id=counterpart_user_id,
        )
        try:
            # 只把会话插入包在保存点里：唯一键冲突只撤销这一条 INSERT，
            # 不回滚调用方整个请求事务（同事务里可能已有订单等业务写入）。
            async with self._db.begin_nested():
                self._db.add(conversation)
                await self._db.flush()
        except IntegrityError as exc:
            existing = await self._recover_order_conversation_conflict(
                exc=exc,
                current_user_id=current_user_id,
                target_user_id=target_user_id,
                conversation_type=conversation_type,
                order_id=order_id,
                counterpart_user_id=counterpart_user_id,
            )
            if existing is None:
                # 不是订单会话唯一键冲突（外键/其他约束）：保持原异常，
                # 由上层按真实错误处理。
                raise
            return existing

        current_is_admin = current_user.role == UserRole.ADMIN
        target_is_admin = target_user.role == UserRole.ADMIN
        self._db.add_all(
            [
                ConversationParticipant(
                    conversation_id=conversation.id,
                    user_id=current_user.id,
                    role_snapshot=current_user.role.value,
                    is_pinned=target_is_admin and not current_is_admin,
                    pinned_at=datetime.now(timezone.utc)
                    if target_is_admin and not current_is_admin
                    else None,
                ),
                ConversationParticipant(
                    conversation_id=conversation.id,
                    user_id=target_user.id,
                    role_snapshot=target_user.role.value,
                    is_pinned=current_is_admin and not target_is_admin,
                    pinned_at=datetime.now(timezone.utc)
                    if current_is_admin and not target_is_admin
                    else None,
                ),
            ]
        )
        await self._db.flush()

        # 带单会话创建时自动写入首条系统消息，承载订单上下文卡片 JSON
        # （标题/价格/段位/#id 链接），并刷新会话 preview 使列表页可见。
        if order_id is not None and conversation_type == ConversationType.ORDER:
            try:
                # 卡片写入也放进保存点：数据库层失败时只撤销这一段，事务
                # 不会进入 failed 状态，后面的会话重载仍可继续执行。
                async with self._db.begin_nested():
                    # 重新解析订单以确保卡片数据可用，避免上方分支变量作用域
                    # 带来的 possibly-undefined 风险，同时拿到最新快照。
                    card_order = await self._get_order_for_conversation(order_id)
                    title_rules = await get_order_title_replacements(self._db)
                    card_meta = self._build_order_card_meta(card_order, title_rules)
                    card_content = self._build_order_card_content(
                        card_order, title_rules
                    )
                    now = datetime.now(timezone.utc)
                    first_msg = Message(
                        conversation_id=conversation.id,
                        sender_id=None,
                        message_type=MessageType.SYSTEM,
                        content=card_content,
                        meta_json=card_meta,
                        created_at=now,
                    )
                    self._db.add(first_msg)
                    await self._db.flush()
                    conversation.last_message_at = first_msg.created_at
                    conversation.last_message_preview = self._build_message_preview(first_msg)
                    await self._db.flush()
            except Exception:
                # 卡片写入失败不阻断会话创建；保存点已回滚，事务仍可用。
                logger.warning(
                    "Failed to write order card system message for order %s",
                    order_id,
                    exc_info=True,
                )

        conversation = await self._get_conversation_or_404(conversation.id)
        conversation.unread_count = 0
        return conversation

    async def _recover_order_conversation_conflict(
        self,
        *,
        exc: IntegrityError,
        current_user_id: int,
        target_user_id: int,
        conversation_type: ConversationType,
        order_id: int | None,
        counterpart_user_id: int | None,
    ) -> Conversation | None:
        """订单会话唯一键冲突后的恢复。

        返回 None 表示异常不是订单会话唯一键冲突，调用方应原样抛出。
        命中目标约束时优先返回「同一订单 + 同一对方」的会话；不同对方
        的正常创建不会命中新唯一键，会直接插入成功，不再返回 409。
        只有同对方冲突后仍查不到会话（真正的异常时序）才返回可操作的 409，
        绝不把用户加入别人的会话，也不泄露会话或对方信息。

        保存点回滚后当前事务仍可能停留在旧的一致性读快照里：并发赢家的
        会话若在本事务首个快照之后才提交，普通 SELECT 看不到。此时整事务
        回滚一次以开启新快照再查；该路径只在并发建联的极端时序出现，
        聊天建联端点在此之前没有其他业务写入。
        """
        if order_id is None or conversation_type != ConversationType.ORDER:
            return None
        if not _is_order_conversation_unique_conflict(exc):
            return None

        existing = await self._find_existing_conversation(
            user_id=current_user_id,
            target_user_id=target_user_id,
            conversation_type=conversation_type,
            order_id=order_id,
            counterpart_user_id=counterpart_user_id,
        )

        if existing is not None:
            return await self._attach_unread_count(existing, current_user_id)

        await self._db.rollback()
        existing = await self._find_existing_conversation(
            user_id=current_user_id,
            target_user_id=target_user_id,
            conversation_type=conversation_type,
            order_id=order_id,
            counterpart_user_id=counterpart_user_id,
        )
        if existing is None:
            # 同订单同对方已发生唯一键冲突，却读不到对应会话：属于异常
            # 时序（例如快照尚未追上并发提交）。给出可操作提示，不泄露
            # 对方身份或任何会话内容。
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "该订单已有其他对话，请到消息中心查看你参与的会话，"
                    "或联系客服协助处理"
                ),
            )
        return await self._attach_unread_count(existing, current_user_id)

    async def _attach_unread_count(
        self,
        conversation: Conversation,
        current_user_id: int,
    ) -> Conversation:
        """重新读取当前用户再算未读，避免使用可能已过期的 ORM 对象。"""
        current_user = await self._get_active_user(current_user_id)
        conversation.unread_count = await self._get_unread_count(
            current_user=current_user,
            conversation_id=conversation.id,
        )
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
            .order_by(
                # MySQL 不支持 NULLS LAST 语法；DESC 排序下 NULL 天然靠后，语义一致
                ConversationParticipant.is_pinned.desc(),
                ConversationParticipant.pinned_at.desc(),
                Conversation.last_message_at.desc(),
                Conversation.id.desc(),
            )
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

        max_size = 10 * 1024 * 1024
        file_bytes = await file.read(max_size + 1)
        if not file_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="上传图片不能为空",
            )

        if len(file_bytes) > max_size:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="图片大小不能超过10MB",
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

    async def set_conversation_pinned(
        self,
        conversation_id: int,
        current_user: User,
        is_pinned: bool,
    ) -> Conversation:
        """Set the current user's pin state for a conversation."""
        participant = await self._get_participant_or_403(
            conversation_id=conversation_id,
            user_id=current_user.id,
        )
        participant.is_pinned = is_pinned
        participant.pinned_at = datetime.now(timezone.utc) if is_pinned else None
        await self._db.flush()
        conversation = await self._get_conversation_or_404(conversation_id)
        conversation.unread_count = await self._get_unread_count(
            current_user=current_user,
            conversation_id=conversation_id,
        )
        return conversation

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

    def _assert_order_contactable(self, order: Order) -> None:
        """若订单不可联络则抛出携带禁止原因的异常。

        覆盖：订单不存在已在上游 404；此处处理 claim_status / 归档 / 截止
        等不可联络状态，返回合适的禁止原因供前端透出。
        """
        if order.is_archived:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="订单已归档，不可发起会话",
            )
        # 终态订单（已完成 / 已取消）整单不再禁止建联：发布者与参与打手
        # 需要售后与复购沟通，截止与抢单状态对已完结的订单也已无意义。
        # 此处直接放行，联络资格改由参与者集合判定，与订单无关的打手
        # 依旧会在下游被拒绝。
        if order.status in (OrderStatus.CANCELLED, OrderStatus.COMPLETED):
            return
        now = datetime.now(timezone.utc)
        if order.deadline is not None:
            dl = order.deadline
            if dl.tzinfo is None:
                dl = dl.replace(tzinfo=timezone.utc)
            if dl <= now:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="订单已截止，不可联系",
                )
        # claim_status 语义与 order_service.accept_order 保持一致
        if order.claim_status == ClaimStatus.PAUSED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="订单已暂停抢单，暂不可联系",
            )
        if order.claim_status == ClaimStatus.CLOSED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="订单已截止，不可联系",
            )
        if order.claim_status == ClaimStatus.FULL:
            # 已满员仍允许已参与打手与客户互聊，但不允许新的 PENDING 咨询
            # 冲击；此处归入可联络，由参与者集合进一步约束。
            pass

    async def _get_order_participant_ids(self, order: Order) -> set[int]:
        """返回订单参与者 id 集合：order.user_id + 首抢 order.booster_id
        + SELECT OrderClaim.booster_id 全部去重，确保第 2 及后续打手也能建联。"""
        ids: set[int] = set()
        if order.user_id is not None:
            ids.add(int(order.user_id))
        if order.booster_id is not None:
            ids.add(int(order.booster_id))
        claim_result = await self._db.execute(
            select(OrderClaim.booster_id).where(OrderClaim.order_id == order.id)
        )
        for bid in claim_result.scalars().all():
            if bid is not None:
                ids.add(int(bid))
        return ids

    def _compute_counterpart_id(
        self,
        *,
        order: Order,
        user_id: int,
        target_user_id: int,
    ) -> int | None:
        """计算 ORDER 会话的对方（发布人之外的一方）。

        建联双方必须至少有一方是订单发布人；两名非发布人之间不会产生
        counterpart，也会在资格校验中被拒绝。
        """
        owner_id = order.user_id
        if owner_id is None:
            return None
        if user_id == owner_id:
            return target_user_id
        if target_user_id == owner_id:
            return user_id
        return None

    def _assert_order_conversation_eligible(
        self,
        *,
        order: Order,
        current_user: User,
        target_user: User,
        participant_ids: set[int],
    ) -> None:
        """新建 ORDER 会话前的服务端权限校验。

        规则：
        - 至少一方必须是订单发布人，两名非发布人一律 403；
        - 非发布人一方必须是活跃的非管理员；
        - 管理员联系发布人（target=发布人）保留客服显式例外，管理员主动
          联系服务方则不允许（历史已有会话在上面已优先返回）；
        - PENDING 接单前咨询：非发布人若不是 order.booster_id、也没有
          OrderClaim，则必须 can_accept=True；已抢单/已接单/终态售后参与者
          按现有参与者规则放行。
        """
        owner_id = order.user_id
        if owner_id is None or (
            current_user.id != owner_id and target_user.id != owner_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只能由订单发布人与服务方发起对话",
            )

        non_publisher = target_user if current_user.id == owner_id else current_user
        if not non_publisher.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="当前账号已停用，无法发起订单对话",
            )

        if non_publisher.role == UserRole.ADMIN:
            admin_contacting_publisher = (
                current_user.role == UserRole.ADMIN
                and target_user.id == owner_id
            )
            if not admin_contacting_publisher:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="管理员只能主动联系订单发布人，不能主动联系服务方",
                )
            # 客服联系发布人的显式例外：不要求管理员是订单参与者。
            return

        if non_publisher.id in participant_ids:
            return

        # 非参与者：只有 PENDING 且仍在开放抢单时允许接单前咨询，且必须
        # 当前可接单。FULL/PAUSED/CLOSED 的订单只允许已参与打手与发布人
        # 继续沟通（_assert_order_contactable 对 PAUSED/CLOSED 已拦，
        # FULL 需要在这里按 claim_status 再拦一次）。
        if order.status == OrderStatus.PENDING and order.claim_status == ClaimStatus.OPEN:
            # 指定派单：非指定人不能在接单前咨询发布人，避免其他用户
            # 把已点名订单当作普通大厅单骚扰发布人。
            if (
                order.designated_booster_id is not None
                and order.designated_booster_id != non_publisher.id
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="该订单已指定给其他用户，暂无法发起订单咨询",
                )
            if not non_publisher.can_accept:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="该账号当前不可接单，暂无法发起订单咨询",
                )
            return

        detail = (
            "当前用户无权基于该订单创建对话"
            if current_user.id != owner_id
            else "目标用户不属于该订单"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )

    def _build_order_card_meta(
        self, order: Order, title_rules: list[dict] | None = None
    ) -> dict:
        """构造订单上下文卡片 JSON，供前端渲染及列表透出。"""
        price_val: str
        try:
            price_val = str(order.price) if order.price is not None else ""
        except Exception:
            price_val = ""
        raw_title = order.title or order.game_name or f"订单 #{order.id}"
        return {
            "type": "order_card",
            "event": "order_context",
            "order_id": int(order.id),
            "title": apply_title_replacements(raw_title, title_rules),
            "game_name": order.game_name,
            "current_rank": order.current_rank,
            "target_rank": order.target_rank,
            "price": price_val,
            "price_min": str(order.price_min) if order.price_min is not None else None,
            "price_max": str(order.price_max) if order.price_max is not None else None,
            "status": order.status.value if hasattr(order.status, "value") else str(order.status),
            "link": f"/orders/{order.id}",
            "anchor": f"#order-{order.id}",
        }

    def _build_order_card_content(
        self, order: Order, title_rules: list[dict] | None = None
    ) -> str:
        """系统消息正文：人类可读的订单上下文，便于 preview 与搜索命中。"""
        meta = self._build_order_card_meta(order, title_rules)
        title = meta["title"]
        rank = f"{order.current_rank} → {order.target_rank}" if order.current_rank and order.target_rank else ""
        link = meta["link"]
        # 标题 / 价格 / 段位 / #id 链接均出现在正文，确保列表 preview 可见
        parts = [f"[订单 #{order.id}] {title}"]
        if rank:
            parts.append(rank)
        if meta.get("price"):
            parts.append(f"价格 {meta['price']}")
        parts.append(f"查看 {link} #{order.id}")
        return " ｜ ".join(part for part in parts if part)

    async def _find_existing_conversation(
        self,
        user_id: int,
        target_user_id: int,
        conversation_type: ConversationType,
        order_id: int | None,
        counterpart_user_id: int | None = None,
    ) -> Conversation | None:
        """查找双方都已参与的会话。

        ORDER 会话优先按 ``(order_id, counterpart_user_id)`` 定位：管理员
        被邀请进三人会话后，发布人与打手的 pair 仍然能命中同一会话。
        新键未命中时回退到「双方都是参与者」的查询，兼容 071 之前
        counterpart_user_id 为 NULL 的历史会话。PRIVATE 同样只要求双方
        都是参与者，不额外判断人数。
        """
        participant_a = aliased(ConversationParticipant)
        participant_b = aliased(ConversationParticipant)
        query = (
            select(Conversation.id)
            .join(
                participant_a,
                and_(
                    participant_a.conversation_id == Conversation.id,
                    participant_a.user_id == user_id,
                ),
            )
            .join(
                participant_b,
                and_(
                    participant_b.conversation_id == Conversation.id,
                    participant_b.user_id == target_user_id,
                ),
            )
            .where(Conversation.type == conversation_type)
        )

        if order_id is None:
            query = query.where(Conversation.order_id.is_(None))
        else:
            query = query.where(Conversation.order_id == order_id)

        if (
            conversation_type == ConversationType.ORDER
            and order_id is not None
            and counterpart_user_id is not None
        ):
            preferred_query = (
                query.where(
                    Conversation.counterpart_user_id == counterpart_user_id
                )
                .order_by(Conversation.id.desc())
                .limit(1)
            )
            result = await self._db.execute(preferred_query)
            conversation_id = result.scalars().first()
            if conversation_id is not None:
                return await self._get_conversation_or_404(conversation_id)

        result = await self._db.execute(
            query.order_by(Conversation.id.desc()).limit(1)
        )
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
            # 订单卡片/取消协商等系统消息只作为聊天记录展示，不计未读、
            # 不触发红点和提醒；只有真实用户消息才占用未读数。
            .where(Message.message_type != MessageType.SYSTEM)
            .where(Message.sender_id.is_not(None))
            .where(Message.sender_id != current_user.id)
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
