"""Payout delay hours: orders gains payout_delay_hours.

到账时效支持"天数自定义放开 + 小时自定义"：在 022 的 payout_delay_days
基础上新增 payout_delay_hours（小时部分 0-23），到期判定为
delivered_at + days + hours。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "023_payout_delay_hours"
down_revision: Union[str, None] = "022_user_publishing_escrow"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("payout_delay_hours", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "payout_delay_hours")
