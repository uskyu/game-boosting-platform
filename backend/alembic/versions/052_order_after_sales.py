"""售后申请（老板对已完成订单发起，管理员审阅划转）

Revision ID: 052_order_after_sales
Revises: 051_notifications_cleanup_index
Create Date: 2026-10-15

新流程：老板对「已完成」订单提交文字原因 + 凭证图片 + 金额（≤订单派单价），
售后单 PENDING 期间该单打手禁止提现；管理员后台通过/驳回，通过时的扣款
划转（打手可用余额 → 老板余额 + 双边流水）由专项逻辑承接。

迁移同时给 notifications.type 的 MySQL ENUM 末尾追加 AFTERSALES_CREATED、
AFTERSALES_RESOLVED 两个值（只追加不重排，保留原有通知流水）。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "052_order_after_sales"
down_revision: Union[str, None] = "051_notifications_cleanup_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 052 之前 notifications.type 的全部取值（046 已追加两个取消协商类型）
_PREVIOUS_NOTIFICATION_TYPES = (
    "'ORDER_ACCEPTED','ORDER_DELIVERED','ORDER_CONFIRMED','ORDER_DISPUTED',"
    "'ORDER_CANCELLED','NEW_MESSAGE','APPLICATION_APPROVED','APPLICATION_REJECTED',"
    "'REVIEW_RECEIVED','SYSTEM_ANNOUNCEMENT',"
    "'ORDER_CANCEL_REQUESTED','ORDER_CANCEL_REQUEST_RESOLVED'"
)
# MySQL ENUM 新增值只追加在末尾，保留原有通知流水。
_NEW_NOTIFICATION_TYPES = (
    _PREVIOUS_NOTIFICATION_TYPES
    + ",'AFTERSALES_CREATED','AFTERSALES_RESOLVED'"
)


def upgrade() -> None:
    op.create_table(
        "order_after_sales",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("requester_id", sa.Integer(), nullable=False),
        sa.Column("booster_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("images", sa.JSON(), nullable=True),
        sa.Column(
            "amount",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
            server_default="0.00",
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "APPROVED",
                "REJECTED",
                name="after_sale_status_enum",
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("decision_reason", sa.String(length=500), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requester_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["booster_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    # 订单维度查待处理售后（列表打标/防重发起）
    op.create_index(
        "ix_after_sales_order_status",
        "order_after_sales",
        ["order_id", "status"],
    )
    # 打手维度查待处理售后（禁提现闸门）
    op.create_index(
        "ix_after_sales_booster_status",
        "order_after_sales",
        ["booster_id", "status"],
    )
    # 后台按状态分页 + 创建时间倒序
    op.create_index(
        "ix_after_sales_status_created",
        "order_after_sales",
        ["status", "created_at"],
    )

    op.execute(
        "ALTER TABLE notifications MODIFY COLUMN `type` "
        f"ENUM({_NEW_NOTIFICATION_TYPES}) NOT NULL"
    )


def downgrade() -> None:
    conn = op.get_bind()
    # 新通知类型回映到既有订单通知类型，再缩短 MySQL ENUM。
    conn.execute(
        sa.text(
            "UPDATE notifications SET `type` = 'ORDER_CANCELLED' "
            "WHERE `type` IN ('AFTERSALES_CREATED', 'AFTERSALES_RESOLVED')"
        )
    )
    op.execute(
        "ALTER TABLE notifications MODIFY COLUMN `type` "
        f"ENUM({_PREVIOUS_NOTIFICATION_TYPES}) NOT NULL"
    )
    op.drop_index("ix_after_sales_status_created", table_name="order_after_sales")
    op.drop_index("ix_after_sales_booster_status", table_name="order_after_sales")
    op.drop_index("ix_after_sales_order_status", table_name="order_after_sales")
    op.drop_table("order_after_sales")
