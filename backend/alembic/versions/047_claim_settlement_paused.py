"""取消/争议处理期间暂停名额到账倒计时。

order_claims 新增 settlement_paused：取消协商挂起或订单争议期间为 True，
调度器（payout_scheduler.scan_due_payouts）只捞 settlement_paused=False 的
DELIVERED 名额，因此暂停期间即使 settlement_due_at 到期也不会自动结算；
协商拒绝/同意、争议撤销后由 service 置回 False 恢复计时。

Revision ID: 047_claim_settlement_paused
Revises: 046_bilateral_cancel_requests
Create Date: 2026-10-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "047_claim_settlement_paused"
down_revision: Union[str, None] = "046_bilateral_cancel_requests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "order_claims",
        sa.Column(
            "settlement_paused",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("order_claims", "settlement_paused")
