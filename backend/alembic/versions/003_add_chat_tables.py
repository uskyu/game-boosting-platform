"""add chat tables

Revision ID: 003_add_chat_tables
Revises: 002_booster_application
Create Date: 2026-03-30_00_00_00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "003_add_chat_tables"
down_revision: Union[str, None] = "002_booster_application"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conversation_type_enum = sa.Enum(
        "PRIVATE",
        "ORDER",
        name="conversation_type_enum",
    )
    conversation_type_enum.create(op.get_bind(), checkfirst=True)

    message_type_enum = sa.Enum(
        "TEXT",
        "IMAGE",
        "SYSTEM",
        name="message_type_enum",
    )
    message_type_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "type",
            sa.Enum("PRIVATE", "ORDER", name="conversation_type_enum"),
            nullable=False,
        ),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("last_message_at", sa.DateTime(), nullable=True),
        sa.Column("last_message_preview", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_conversations_order_id_orders"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )
    op.create_index(op.f("ix_conversations_id"), "conversations", ["id"], unique=False)
    op.create_index(op.f("ix_conversations_type"), "conversations", ["type"], unique=False)
    op.create_index(op.f("ix_conversations_order_id"), "conversations", ["order_id"], unique=False)
    op.create_index(
        op.f("ix_conversations_last_message_at"),
        "conversations",
        ["last_message_at"],
        unique=False,
    )

    op.create_table(
        "conversation_participants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role_snapshot", sa.String(length=20), nullable=False),
        sa.Column(
            "joined_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("last_read_message_id", sa.Integer(), nullable=True),
        sa.Column("last_read_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_conversation_participants_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_conversation_participants_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_participants")),
        sa.UniqueConstraint(
            "conversation_id",
            "user_id",
            name="uq_conversation_participants_conversation_id_user_id",
        ),
    )
    op.create_index(
        op.f("ix_conversation_participants_id"),
        "conversation_participants",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_conversation_participants_conversation_id"),
        "conversation_participants",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_conversation_participants_user_id"),
        "conversation_participants",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("sender_id", sa.Integer(), nullable=True),
        sa.Column(
            "message_type",
            sa.Enum("TEXT", "IMAGE", "SYSTEM", name="message_type_enum"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("recalled_at", sa.DateTime(), nullable=True),
        sa.Column("meta_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sender_id"],
            ["users.id"],
            name=op.f("fk_messages_sender_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
    )
    op.create_index(op.f("ix_messages_id"), "messages", ["id"], unique=False)
    op.create_index(op.f("ix_messages_conversation_id"), "messages", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_messages_sender_id"), "messages", ["sender_id"], unique=False)
    op.create_index(op.f("ix_messages_message_type"), "messages", ["message_type"], unique=False)
    op.create_index(
        "ix_messages_conversation_created_at",
        "messages",
        ["conversation_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_messages_conversation_id_id",
        "messages",
        ["conversation_id", "id"],
        unique=False,
    )

    op.create_table(
        "message_deletions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "deleted_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_message_deletions_message_id_messages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_message_deletions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message_deletions")),
        sa.UniqueConstraint(
            "message_id",
            "user_id",
            name="uq_message_deletions_message_id_user_id",
        ),
    )
    op.create_index(op.f("ix_message_deletions_id"), "message_deletions", ["id"], unique=False)
    op.create_index(
        op.f("ix_message_deletions_message_id"),
        "message_deletions",
        ["message_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_message_deletions_user_id"),
        "message_deletions",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_message_deletions_user_id"), table_name="message_deletions")
    op.drop_index(op.f("ix_message_deletions_message_id"), table_name="message_deletions")
    op.drop_index(op.f("ix_message_deletions_id"), table_name="message_deletions")
    op.drop_table("message_deletions")

    op.drop_index("ix_messages_conversation_id_id", table_name="messages")
    op.drop_index("ix_messages_conversation_created_at", table_name="messages")
    op.drop_index(op.f("ix_messages_message_type"), table_name="messages")
    op.drop_index(op.f("ix_messages_sender_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_conversation_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_id"), table_name="messages")
    op.drop_table("messages")

    op.drop_index(op.f("ix_conversation_participants_user_id"), table_name="conversation_participants")
    op.drop_index(op.f("ix_conversation_participants_conversation_id"), table_name="conversation_participants")
    op.drop_index(op.f("ix_conversation_participants_id"), table_name="conversation_participants")
    op.drop_table("conversation_participants")

    op.drop_index(op.f("ix_conversations_last_message_at"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_order_id"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_type"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_id"), table_name="conversations")
    op.drop_table("conversations")

    sa.Enum(name="message_type_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="conversation_type_enum").drop(op.get_bind(), checkfirst=True)
