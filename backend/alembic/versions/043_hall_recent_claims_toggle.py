"""大厅「今日已接单」区块全站开关：site_settings.hall_recent_claims_enabled

默认开启（server_default=1）：部署后大厅立即可见今日已接单区块，
管理员可在后台站点设置里整体关闭（对所有人生效）。

Revision ID: 043_hall_recent_claims_toggle
Revises: 042_withdrawal_payout_batches
Create Date: 2026-10-05 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "043_hall_recent_claims_toggle"
down_revision: Union[str, None] = "042_withdrawal_payout_batches"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "site_settings",
        sa.Column(
            "hall_recent_claims_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("site_settings", "hall_recent_claims_enabled")
