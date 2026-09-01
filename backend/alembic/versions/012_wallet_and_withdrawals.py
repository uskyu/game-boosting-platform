"""Add wallets, wallet_transactions and withdrawal_requests tables.

Revision ID: 012_wallet_and_withdrawals
Revises: 011_add_notif_and_prefs
Create Date: 2026-09-01
"""

import sqlalchemy as sa
from alembic import op

revision = "012_wallet_and_withdrawals"
down_revision = "011_add_notif_and_prefs"
branch_labels = None
depends_on = None

wallet_transaction_type_enum = sa.Enum(
    "ORDER_INCOME",
    "ADMIN_ADJUST",
    "WITHDRAWAL_FREEZE",
    "WITHDRAWAL_REFUND",
    "WITHDRAWAL_PAID",
    name="wallet_transaction_type_enum",
)

withdrawal_channel_enum = sa.Enum(
    "ALIPAY",
    "WECHAT",
    "BANK",
    name="withdrawal_channel_enum",
)

withdrawal_status_enum = sa.Enum(
    "PENDING",
    "APPROVED",
    "REJECTED",
    "PAID",
    name="withdrawal_status_enum",
)


def upgrade() -> None:
    wallet_transaction_type_enum.create(op.get_bind(), checkfirst=True)
    withdrawal_channel_enum.create(op.get_bind(), checkfirst=True)
    withdrawal_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "wallets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("available_balance", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("frozen_balance", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("total_income", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("total_withdrawn", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE",
            name="fk_wallets_user_id_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_wallets"),
        sa.UniqueConstraint("user_id", name="uq_wallets_user_id"),
    )
    op.create_index("ix_wallets_id", "wallets", ["id"])
    op.create_index("ix_wallets_user_id", "wallets", ["user_id"])

    op.create_table(
        "withdrawal_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("channel", withdrawal_channel_enum, nullable=False),
        sa.Column("account_name", sa.String(64), nullable=False),
        sa.Column("account_no", sa.String(128), nullable=False),
        sa.Column("status", withdrawal_status_enum, nullable=False, server_default="PENDING"),
        sa.Column("reject_reason", sa.String(255), nullable=True),
        sa.Column("payment_reference", sa.String(128), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("paid_by", sa.Integer(), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE",
            name="fk_withdrawal_requests_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"], ["users.id"], ondelete="SET NULL",
            name="fk_withdrawal_requests_reviewed_by_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_withdrawal_requests"),
    )
    op.create_index("ix_withdrawal_requests_id", "withdrawal_requests", ["id"])
    op.create_index("ix_withdrawal_requests_user_id", "withdrawal_requests", ["user_id"])
    op.create_index("ix_withdrawal_requests_status", "withdrawal_requests", ["status"])
    op.create_index(
        "ix_withdrawal_user_created", "withdrawal_requests", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_withdrawal_status_created", "withdrawal_requests", ["status", "created_at"]
    )

    op.create_table(
        "wallet_transactions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("wallet_id", sa.Integer(), nullable=False),
        sa.Column("type", wallet_transaction_type_enum, nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("balance_before", sa.Numeric(12, 2), nullable=False),
        sa.Column("balance_after", sa.Numeric(12, 2), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("withdrawal_id", sa.Integer(), nullable=True),
        sa.Column("operator_id", sa.Integer(), nullable=True),
        sa.Column("remark", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["wallet_id"], ["wallets.id"], ondelete="CASCADE",
            name="fk_wallet_transactions_wallet_id_wallets",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], ondelete="SET NULL",
            name="fk_wallet_transactions_order_id_orders",
        ),
        sa.ForeignKeyConstraint(
            ["withdrawal_id"], ["withdrawal_requests.id"], ondelete="SET NULL",
            name="fk_wallet_transactions_withdrawal_id_withdrawal_requests",
        ),
        sa.ForeignKeyConstraint(
            ["operator_id"], ["users.id"], ondelete="SET NULL",
            name="fk_wallet_transactions_operator_id_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_wallet_transactions"),
        sa.UniqueConstraint("order_id", "type", name="uq_wallet_tx_order_type"),
    )
    op.create_index("ix_wallet_transactions_id", "wallet_transactions", ["id"])
    op.create_index("ix_wallet_transactions_wallet_id", "wallet_transactions", ["wallet_id"])
    op.create_index("ix_wallet_transactions_order_id", "wallet_transactions", ["order_id"])
    op.create_index(
        "ix_wallet_transactions_withdrawal_id", "wallet_transactions", ["withdrawal_id"]
    )
    op.create_index(
        "ix_wallet_tx_wallet_created", "wallet_transactions", ["wallet_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("wallet_transactions")
    op.drop_table("withdrawal_requests")
    op.drop_table("wallets")
    withdrawal_status_enum.drop(op.get_bind(), checkfirst=True)
    withdrawal_channel_enum.drop(op.get_bind(), checkfirst=True)
    wallet_transaction_type_enum.drop(op.get_bind(), checkfirst=True)
