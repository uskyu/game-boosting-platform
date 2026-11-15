"""conversations 增加 counterpart_user_id（expand 阶段）

Revision ID: 071_chat_counterpart_expand
Revises: 070_publish_permission_global
Create Date: 2026-11-15 10:00:00.000000

背景：
同一订单原先只允许一条会话（``uq_conversations_order_id``），导致打手 A
与老板聊过之后，打手 B 接单时无法再与老板建立自己的私聊。新规则改为
「同一订单 + 同一对方」唯一：ORDER 会话记录非发布人一方的实际参与者
``counterpart_user_id``，不同打手各自拥有独立会话。

本迁移是 expand 阶段：
1. 新增 nullable 列 ``counterpart_user_id``（FK users.id, ON DELETE SET NULL）；
2. 回填所有 ORDER 会话：取「非发布人、非管理员、唯一」的参与者；
3. 校验回填结果（不得有非法 NULL、不得等于发布人、不得重复）；
4. 新增 ``uq_conversations_order_counterpart(order_id, counterpart_user_id)``；
5. **保留**旧的 ``uq_conversations_order_id``，待 072 contract 再删除。

任何校验不通过都会抛错中止，不会删除旧唯一键。MySQL DDL 非事务，脚本对
列/索引/FK 做了存在性判断，可在修正数据后重跑。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "071_chat_counterpart_expand"
down_revision: Union[str, None] = "070_publish_permission_global"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMN = "counterpart_user_id"
_INDEX = "ix_conversations_counterpart_user_id"
_FK = "fk_conversations_counterpart_user_id_users"
_UNIQUE = "uq_conversations_order_counterpart"
_OLD_UNIQUE = "uq_conversations_order_id"


def _column_exists(bind, table: str, column: str) -> bool:
    result = bind.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = :table
              AND column_name = :column
            """
        ),
        {"table": table, "column": column},
    )
    return int(result.scalar() or 0) > 0


def _index_exists(bind, table: str, index_name: str) -> bool:
    result = bind.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND table_name = :table
              AND index_name = :index_name
            """
        ),
        {"table": table, "index_name": index_name},
    )
    return int(result.scalar() or 0) > 0


def _fk_exists(bind, table: str, fk_name: str) -> bool:
    result = bind.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM information_schema.table_constraints
            WHERE table_schema = DATABASE()
              AND table_name = :table
              AND constraint_name = :fk_name
              AND constraint_type = 'FOREIGN KEY'
            """
        ),
        {"table": table, "fk_name": fk_name},
    )
    return int(result.scalar() or 0) > 0


def _backfill_counterpart(bind) -> None:
    rows = bind.execute(
        sa.text(
            """
            SELECT c.id, o.user_id AS owner_id
            FROM conversations c
            JOIN orders o ON o.id = c.order_id
            WHERE c.type = 'ORDER'
              AND c.order_id IS NOT NULL
            """
        )
    ).mappings().all()

    for row in rows:
        conversation_id = row["id"]
        owner_id = row["owner_id"]
        # 对方必须是非发布人、非管理员，且在会话参与者中唯一。只看加入时的
        # role_snapshot：打手后来被提升为管理员仍是这条会话的实际对方；
        # 只有“以管理员身份被邀请进会话”的参与者才排除。不能看 users.role
        # 当前值，否则角色变更会让回填找不到候选而中止迁移。
        candidates = bind.execute(
            sa.text(
                """
                SELECT cp.user_id
                FROM conversation_participants cp
                WHERE cp.conversation_id = :conversation_id
                  AND cp.user_id <> :owner_id
                  AND cp.role_snapshot <> 'ADMIN'
                GROUP BY cp.user_id
                """
            ),
            {"conversation_id": conversation_id, "owner_id": owner_id},
        ).scalars().all()

        if len(candidates) != 1:
            raise RuntimeError(
                "conversations 回填中止：会话 "
                f"{conversation_id} 找到 {len(candidates)} 个非发布人非管理员参与者，"
                "要求恰好 1 个。请先人工核对该会话参与者后再执行迁移。"
            )

        bind.execute(
            sa.text(
                """
                UPDATE conversations
                SET counterpart_user_id = :counterpart_user_id
                WHERE id = :conversation_id
                """
            ),
            {
                "counterpart_user_id": int(candidates[0]),
                "conversation_id": conversation_id,
            },
        )


def _assert_valid(bind) -> None:
    null_count = int(
        bind.execute(
            sa.text(
                """
                SELECT COUNT(*) FROM conversations
                WHERE type = 'ORDER'
                  AND order_id IS NOT NULL
                  AND counterpart_user_id IS NULL
                """
            )
        ).scalar()
        or 0
    )
    if null_count:
        raise RuntimeError(
            f"conversations 回填中止：仍有 {null_count} 条 ORDER 会话缺少 "
            "counterpart_user_id。"
        )

    owner_count = int(
        bind.execute(
            sa.text(
                """
                SELECT COUNT(*) FROM conversations c
                JOIN orders o ON o.id = c.order_id
                WHERE c.type = 'ORDER'
                  AND c.counterpart_user_id = o.user_id
                """
            )
        ).scalar()
        or 0
    )
    if owner_count:
        raise RuntimeError(
            f"conversations 回填中止：{owner_count} 条会话的 counterpart_user_id "
            "等于订单发布人。"
        )

    duplicate_orders = bind.execute(
        sa.text(
            """
            SELECT order_id, counterpart_user_id, COUNT(*) AS row_count
            FROM conversations
            WHERE type = 'ORDER'
              AND order_id IS NOT NULL
            GROUP BY order_id, counterpart_user_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate_orders is not None:
        raise RuntimeError(
            "conversations 回填中止：订单 "
            f"{duplicate_orders[0]} / 对方 {duplicate_orders[1]} 存在 "
            f"{duplicate_orders[2]} 条重复会话。"
        )


def upgrade() -> None:
    bind = op.get_bind()

    if not _column_exists(bind, "conversations", _COLUMN):
        op.add_column(
            "conversations",
            sa.Column(
                _COLUMN,
                sa.Integer(),
                nullable=True,
                comment="ORDER 会话中发布人之外的实际对方；PRIVATE 为 NULL",
            ),
        )

    if not _index_exists(bind, "conversations", _INDEX):
        op.create_index(_INDEX, "conversations", [_COLUMN], unique=False)

    if not _fk_exists(bind, "conversations", _FK):
        op.create_foreign_key(
            _FK,
            "conversations",
            "users",
            [_COLUMN],
            ["id"],
            ondelete="SET NULL",
        )

    _backfill_counterpart(bind)
    _assert_valid(bind)

    if not _index_exists(bind, "conversations", _UNIQUE):
        op.create_unique_constraint(
            _UNIQUE,
            "conversations",
            ["order_id", _COLUMN],
        )


def downgrade() -> None:
    bind = op.get_bind()

    if _index_exists(bind, "conversations", _UNIQUE):
        op.drop_constraint(_UNIQUE, "conversations", type_="unique")

    if _fk_exists(bind, "conversations", _FK):
        op.drop_constraint(_FK, "conversations", type_="foreignkey")

    if _index_exists(bind, "conversations", _INDEX):
        op.drop_index(_INDEX, table_name="conversations")

    if _column_exists(bind, "conversations", _COLUMN):
        op.drop_column("conversations", _COLUMN)
