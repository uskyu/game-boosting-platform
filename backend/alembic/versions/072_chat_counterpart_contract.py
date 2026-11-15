"""删除 conversations 的旧「每单一条」唯一键（contract 阶段）

Revision ID: 072_chat_counterpart_contract
Revises: 071_chat_counterpart_expand
Create Date: 2026-11-15 10:10:00.000000

071 已回填 counterpart_user_id 并新增 (order_id, counterpart_user_id) 唯一键。
071 与新代码上线之间旧 backend 仍可能新建 counterpart 为 NULL 的 ORDER 会话，
本迁移先按同样的「非发布人、非管理员（按加入时角色）」规则补齐这些行，再
校验不存在非法 NULL/发布人自指/重复，最后删除旧的 uq_conversations_order_id，
允许同一订单下发布人与不同打手各自拥有独立会话。

部署注意：本迁移会删除旧唯一键，不能在使用旧 backend 在线服务的“预跑
migrations”阶段直接执行；应只预跑 071，待旧 backend 停止、新容器启动时由
entrypoint 执行 072，或直接停 backend 后按顺序执行 071 → 072。

downgrade 需要恢复旧唯一键；若此时同一订单已存在多条会话，旧键无法恢复，
脚本会抛错中止并提示先合并/清理，而不是静默丢数据。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "072_chat_counterpart_contract"
down_revision: Union[str, None] = "071_chat_counterpart_expand"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_UNIQUE = "uq_conversations_order_id"


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


def _backfill_counterpart(bind) -> None:
    """补齐 071 之后旧代码仍可能新建的 ORDER 会话。

    expand 阶段旧 backend 仍在服务，旧代码不会写 counterpart_user_id；
    本阶段只处理仍为 NULL 的 ORDER 行，取“非发布人、非管理员（按加入时
    角色快照）、唯一”的参与者。找不到唯一候选就中止，不删旧唯一键。
    """
    rows = bind.execute(
        sa.text(
            """
            SELECT c.id, o.user_id AS owner_id
            FROM conversations c
            JOIN orders o ON o.id = c.order_id
            WHERE c.type = 'ORDER'
              AND c.order_id IS NOT NULL
              AND c.counterpart_user_id IS NULL
            """
        )
    ).mappings().all()

    for row in rows:
        conversation_id = row["id"]
        owner_id = row["owner_id"]
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
                "conversations contract 回填中止：会话 "
                f"{conversation_id} 找到 {len(candidates)} 个非发布人非管理员参与者，"
                "要求恰好 1 个。请先人工核对该会话参与者后再执行 072。"
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
            "conversations contract 中止：仍有 "
            f"{null_count} 条 ORDER 会话缺少 counterpart_user_id，"
            f"保留旧唯一键 {_OLD_UNIQUE}。"
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
            f"conversations contract 中止：{owner_count} 条会话的 "
            "counterpart_user_id 等于订单发布人。"
        )

    duplicate = bind.execute(
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
    if duplicate is not None:
        raise RuntimeError(
            "conversations contract 中止：订单 "
            f"{duplicate[0]} / 对方 {duplicate[1]} 存在 {duplicate[2]} 条重复会话。"
        )


def upgrade() -> None:
    bind = op.get_bind()
    _backfill_counterpart(bind)
    _assert_valid(bind)

    if _index_exists(bind, "conversations", _OLD_UNIQUE):
        op.drop_constraint(_OLD_UNIQUE, "conversations", type_="unique")


def downgrade() -> None:
    bind = op.get_bind()

    duplicate_orders = bind.execute(
        sa.text(
            """
            SELECT order_id, COUNT(*) AS row_count
            FROM conversations
            WHERE order_id IS NOT NULL
            GROUP BY order_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate_orders is not None:
        raise RuntimeError(
            "conversations contract 降级中止：订单 "
            f"{duplicate_orders[0]} 已有 {duplicate_orders[1]} 条会话，"
            f"无法恢复旧唯一键 {_OLD_UNIQUE}。请先合并或清理同订单会话后再降级。"
        )

    if not _index_exists(bind, "conversations", _OLD_UNIQUE):
        op.create_unique_constraint(
            _OLD_UNIQUE,
            "conversations",
            ["order_id"],
        )
