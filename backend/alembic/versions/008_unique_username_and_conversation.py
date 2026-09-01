"""add unique constraints on users.username and conversations.order_id

Revision ID: 008_unique_constraints
Revises: 007_add_payment_and_reviews
Create Date: 2026-04-06_00_00_00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "008_unique_constraints"
down_revision: Union[str, None] = "007_add_payment_and_reviews"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index_exists(bind, table: str, index_name: str) -> bool:
    """Check if an index already exists (MySQL DDL is non-transactional,
    so a partially-failed migration may have left the index behind)."""
    result = bind.execute(
        sa.text("SHOW INDEX FROM `{t}` WHERE Key_name = :name".format(t=table)),
        {"name": index_name},
    )
    return result.first() is not None


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # users.username must be unique
    # ------------------------------------------------------------------
    bind.execute(
        sa.text(
            """
            UPDATE users AS u
            JOIN (
                SELECT id, username
                FROM (
                    SELECT
                        id,
                        username,
                        ROW_NUMBER() OVER (
                            PARTITION BY username ORDER BY id ASC
                        ) AS rn
                    FROM users
                ) ranked
                WHERE rn > 1
            ) AS dups ON dups.id = u.id
            SET u.username = CONCAT(u.username, '_', u.id)
            """
        )
    )

    if not _index_exists(bind, "users", "uq_users_username"):
        op.create_index(
            "uq_users_username",
            "users",
            ["username"],
            unique=True,
        )

    # ------------------------------------------------------------------
    # conversations.order_id must be unique
    # ------------------------------------------------------------------
    bind.execute(
        sa.text(
            """
            DELETE c
            FROM conversations c
            JOIN (
                SELECT order_id, MIN(id) AS keep_id
                FROM conversations
                WHERE order_id IS NOT NULL
                GROUP BY order_id
                HAVING COUNT(*) > 1
            ) dups
              ON c.order_id = dups.order_id
             AND c.id <> dups.keep_id
            """
        )
    )

    if not _index_exists(bind, "conversations", "uq_conversations_order_id"):
        op.create_unique_constraint(
            "uq_conversations_order_id",
            "conversations",
            ["order_id"],
        )


def downgrade() -> None:
    op.drop_constraint(
        "uq_conversations_order_id",
        "conversations",
        type_="unique",
    )
    op.drop_index("uq_users_username", table_name="users")
