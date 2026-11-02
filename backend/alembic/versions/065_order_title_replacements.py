"""订单标题关键词替换配置与历史数据清洗

Revision ID: 065_order_title_replacements
Revises: 064_withdrawal_fee
Create Date: 2026-11-02 18:20:00.000000

1. site_settings 增加 order_title_replacements JSON 列；
   NULL 表示沿用默认规则（名牌/明牌 → 【MP】）。
2. 历史订单标题、聊天订单系统消息与会话预览按默认规则清洗一次。
   之后新写入的标题/卡片由应用层按后台当前规则替换。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "065_order_title_replacements"
down_revision: Union[str, None] = "064_withdrawal_fee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _replace_brand_words(column: str) -> str:
    return f"REPLACE(REPLACE({column}, '名牌', '【MP】'), '明牌', '【MP】')"


def upgrade() -> None:
    op.add_column(
        "site_settings",
        sa.Column(
            "order_title_replacements",
            sa.JSON(),
            nullable=True,
            comment="订单标题关键词替换规则；NULL=默认规则",
        ),
    )

    condition = "(title LIKE '%名牌%' OR title LIKE '%明牌%')"
    op.execute(
        f"UPDATE orders SET title = LEFT({_replace_brand_words('title')}, 200) "
        f"WHERE {condition}"
    )

    # 聊天里的订单系统消息：正文与 order_card meta 的 title 同步清洗。
    op.execute(
        "UPDATE messages SET content = "
        f"{_replace_brand_words('content')} "
        "WHERE message_type = 'SYSTEM' "
        "AND (content LIKE '%名牌%' OR content LIKE '%明牌%')"
    )
    meta_title = "JSON_UNQUOTE(JSON_EXTRACT(meta_json, '$.title'))"
    meta_title_replaced = _replace_brand_words(meta_title)
    op.execute(
        "UPDATE messages SET meta_json = JSON_SET("
        "meta_json, '$.title', " + meta_title_replaced + ") "
        "WHERE JSON_UNQUOTE(JSON_EXTRACT(meta_json, '$.type')) = 'order_card' "
        "AND (JSON_UNQUOTE(JSON_EXTRACT(meta_json, '$.title')) LIKE '%名牌%' "
        "OR JSON_UNQUOTE(JSON_EXTRACT(meta_json, '$.title')) LIKE '%明牌%')"
    )
    op.execute(
        "UPDATE conversations SET last_message_preview = "
        f"LEFT({_replace_brand_words('last_message_preview')}, 100) "
        "WHERE last_message_preview LIKE '%名牌%' "
        "OR last_message_preview LIKE '%明牌%'"
    )


def downgrade() -> None:
    # 关键词替换不可逆：不尝试把【MP】还原成原文，只移除配置列。
    op.drop_column("site_settings", "order_title_replacements")
