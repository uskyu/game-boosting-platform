"""
Chat models module.
Defines conversation and message related entities.
"""

from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import JSON, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.order import Order
    from app.models.user import User


class ConversationType(str, PyEnum):
    """Conversation type enum."""

    PRIVATE = "PRIVATE"
    ORDER = "ORDER"


class MessageType(str, PyEnum):
    """Message type enum."""

    TEXT = "TEXT"
    IMAGE = "IMAGE"
    SYSTEM = "SYSTEM"


class Conversation(TimestampMixin, Base):
    """Conversation model."""

    __tablename__ = "conversations"
    # One conversation per order: prevents races in
    # create_or_get_conversation from spawning duplicate order chats.
    # (In MySQL, NULL values don't collide, so PRIVATE conversations are
    # unaffected and serialized separately via user row locks.)
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_conversations_order_id"),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        index=True,
    )

    type: Mapped[ConversationType] = mapped_column(
        Enum(
            ConversationType,
            name="conversation_type_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        index=True,
    )

    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    last_message_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        index=True,
    )

    last_message_preview: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    order: Mapped[Optional["Order"]] = relationship(
        "Order",
        lazy="joined",
    )

    participants: Mapped[list["ConversationParticipant"]] = relationship(
        "ConversationParticipant",
        back_populates="conversation",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="conversation",
        lazy="noload",
        cascade="all, delete-orphan",
        order_by="Message.id.asc()",
    )


class ConversationParticipant(Base):
    """Conversation participant model."""

    __tablename__ = "conversation_participants"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "user_id",
            name="uq_conversation_participants_conversation_id_user_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        index=True,
    )

    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    role_snapshot: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    joined_at: Mapped[datetime] = mapped_column(
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    last_read_message_id: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    last_read_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
    )

    conversation: Mapped["Conversation"] = relationship(
        "Conversation",
        back_populates="participants",
        lazy="joined",
    )

    user: Mapped["User"] = relationship(
        "User",
        lazy="joined",
    )


class Message(Base):
    """Conversation message model."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        index=True,
    )

    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    sender_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    message_type: Mapped[MessageType] = mapped_column(
        Enum(
            MessageType,
            name="message_type_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        index=True,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    recalled_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
    )

    meta_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    conversation: Mapped["Conversation"] = relationship(
        "Conversation",
        back_populates="messages",
        lazy="joined",
    )

    sender: Mapped[Optional["User"]] = relationship(
        "User",
        lazy="joined",
    )

    deletions: Mapped[list["MessageDeletion"]] = relationship(
        "MessageDeletion",
        back_populates="message",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class MessageDeletion(Base):
    """Soft deletion record for message."""

    __tablename__ = "message_deletions"
    __table_args__ = (
        UniqueConstraint(
            "message_id",
            "user_id",
            name="uq_message_deletions_message_id_user_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        index=True,
    )

    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    deleted_at: Mapped[datetime] = mapped_column(
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    message: Mapped["Message"] = relationship(
        "Message",
        back_populates="deletions",
        lazy="joined",
    )

    user: Mapped["User"] = relationship(
        "User",
        lazy="joined",
    )
