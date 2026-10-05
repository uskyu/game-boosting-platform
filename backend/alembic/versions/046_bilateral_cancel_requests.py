"""双边取消申请（按接单名额）+ 请求/裁决通知类型。

新流程：发单员或对应打手填写原因与保证金赔偿提议，另一方同意/拒绝；
拒绝不改变订单/名额原状态；同意后只取消目标名额。迁移同时把旧版
“打手申请取消→订单 DISPUTED 等管理员处理”记录迁成待发单员确认的请求，
并恢复订单原进行状态（仅针对可识别的旧请求备注前缀）。

Revision ID: 046_bilateral_cancel_requests
Revises: 045_fee_toggle_delivery_reject
Create Date: 2026-10-05
"""
from __future__ import annotations

import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "046_bilateral_cancel_requests"
down_revision: Union[str, None] = "045_fee_toggle_delivery_reject"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_NOTIFICATION_TYPES = (
    "'ORDER_ACCEPTED','ORDER_DELIVERED','ORDER_CONFIRMED','ORDER_DISPUTED',"
    "'ORDER_CANCELLED','NEW_MESSAGE','APPLICATION_APPROVED','APPLICATION_REJECTED',"
    "'REVIEW_RECEIVED','SYSTEM_ANNOUNCEMENT'"
)
_NEW_NOTIFICATION_TYPES = (
    _OLD_NOTIFICATION_TYPES
    + ",'ORDER_CANCEL_REQUESTED','ORDER_CANCEL_REQUEST_RESOLVED'"
)

_LEGACY_BOOSTER_CANCEL = re.compile(r"^打手取消申请（(.+?)）：(.*)$", re.DOTALL)


def upgrade() -> None:
    op.create_table(
        "order_cancel_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column("requester_id", sa.Integer(), nullable=False),
        sa.Column("recipient_id", sa.Integer(), nullable=False),
        sa.Column("requester_role", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "compensation_amount",
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
                "CANCELLED",
                name="order_cancel_request_status_enum",
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("decision_reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["claim_id"], ["order_claims.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requester_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_cancel_requests_order_status",
        "order_cancel_requests",
        ["order_id", "status"],
    )
    op.create_index(
        "ix_cancel_requests_claim_status",
        "order_cancel_requests",
        ["claim_id", "status"],
    )
    op.create_index(
        "ix_cancel_requests_recipient_status",
        "order_cancel_requests",
        ["recipient_id", "status"],
    )

    # MySQL ENUM 新增值只追加在末尾，保留原有通知流水。
    op.execute(
        "ALTER TABLE notifications MODIFY COLUMN `type` "
        f"ENUM({_NEW_NOTIFICATION_TYPES}) NOT NULL"
    )

    # 兼容 d863fca 旧版已存在的打手申请：旧逻辑只把原因写进 notes 并将订单
    # 置 DISPUTED。本次将可识别记录回填为待发单员处理，并恢复其原进行状态，
    # 避免升级后老板仍被迫去管理员页。未能唯一匹配打手名额的记录保持原状，
    # 由既有派单台做人工兜底。
    conn = op.get_bind()
    legacy_orders = conn.execute(
        sa.text(
            "SELECT id, user_id, notes, delivered_at, updated_at "
            "FROM orders "
            "WHERE status = 'DISPUTED' AND notes LIKE '打手取消申请（%'"
        )
    ).mappings().all()
    for order in legacy_orders:
        notes = order["notes"] or ""
        match = _LEGACY_BOOSTER_CANCEL.match(notes)
        if not match:
            continue
        booster_username = match.group(1).strip()
        reason = match.group(2).splitlines()[0].strip()[:500]
        if len(reason) < 3:
            continue
        claim_rows = conn.execute(
            sa.text(
                "SELECT c.id AS claim_id, c.booster_id, u.username "
                "FROM order_claims AS c JOIN users AS u ON u.id = c.booster_id "
                "WHERE c.order_id = :order_id AND c.status IN ('CLAIMED','DELIVERED')"
            ),
            {"order_id": order["id"]},
        ).mappings().all()
        matches = [row for row in claim_rows if row["username"] == booster_username]
        if len(matches) != 1:
            continue
        claim = matches[0]
        original_status = "DELIVERED" if order["delivered_at"] is not None else "LOCKED"
        conn.execute(
            sa.text(
                "INSERT INTO order_cancel_requests "
                "(order_id, claim_id, requester_id, recipient_id, requester_role, "
                "reason, compensation_amount, status, created_at) "
                "VALUES (:order_id, :claim_id, :requester_id, :recipient_id, 'BOOSTER', "
                ":reason, 0.00, 'PENDING', :created_at)"
            ),
            {
                "order_id": order["id"],
                "claim_id": claim["claim_id"],
                "requester_id": claim["booster_id"],
                "recipient_id": order["user_id"],
                "reason": reason,
                "created_at": order["updated_at"],
            },
        )
        conn.execute(
            sa.text(
                "UPDATE orders SET status = :status, updated_at = NOW() "
                "WHERE id = :order_id AND status = 'DISPUTED'"
            ),
            {"status": original_status, "order_id": order["id"]},
        )


def downgrade() -> None:
    conn = op.get_bind()
    # 新通知类型回映到既有取消/争议类型，再缩短 MySQL ENUM。
    conn.execute(
        sa.text(
            "UPDATE notifications SET `type` = 'ORDER_CANCELLED' "
            "WHERE `type` = 'ORDER_CANCEL_REQUEST_RESOLVED'"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE notifications SET `type` = 'ORDER_DISPUTED' "
            "WHERE `type` = 'ORDER_CANCEL_REQUESTED'"
        )
    )
    op.execute(
        "ALTER TABLE notifications MODIFY COLUMN `type` "
        f"ENUM({_OLD_NOTIFICATION_TYPES}) NOT NULL"
    )
    op.drop_index("ix_cancel_requests_recipient_status", table_name="order_cancel_requests")
    op.drop_index("ix_cancel_requests_claim_status", table_name="order_cancel_requests")
    op.drop_index("ix_cancel_requests_order_status", table_name="order_cancel_requests")
    op.drop_table("order_cancel_requests")
