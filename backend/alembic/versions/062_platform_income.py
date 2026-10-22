"""platform_income: 平台服务费收入流水（仅后台可见）

Revision ID: 062_platform_income
Revises: 061_user_sessions
Create Date: 2026-10-22 19:10:00.000000

修复历史 bug：发单按毛额冻结、结单只扣打手净额，服务费差额一直滞留在
发单人冻结余额里且平台没有入账记录。本表记录每单每名额的服务费，
唯一键保证重试不重复入账。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "062_platform_income"
down_revision: Union[str, None] = "061_user_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "platform_income",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("booster_id", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("service_fee_rate", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default="SERVICE_FEE",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["booster_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "order_id", "booster_id", "source", name="uq_platform_income_order_booster_source"
        ),
    )
    op.create_index("ix_platform_income_order_id", "platform_income", ["order_id"])
    op.create_index("ix_platform_income_booster_id", "platform_income", ["booster_id"])
    op.create_index("ix_platform_income_created_at", "platform_income", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_platform_income_created_at", table_name="platform_income")
    op.drop_index("ix_platform_income_booster_id", table_name="platform_income")
    op.drop_index("ix_platform_income_order_id", table_name="platform_income")
    op.drop_table("platform_income")
