"""Lower the default minimum recharge amount to 1 分钱 (0.01 元).

方便小额测试：默认最低充值金额由 1.00 元下调为 0.01 元。
只调整列默认值，不修改任何已配置的实际值（管理员仍可在后台自行调整）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "031_min_recharge_one_cent"
down_revision: Union[str, None] = "030_payment_method_toggles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "payment_settings",
        "min_amount",
        existing_type=sa.Numeric(precision=12, scale=2),
        existing_nullable=False,
        server_default="0.01",
    )


def downgrade() -> None:
    op.alter_column(
        "payment_settings",
        "min_amount",
        existing_type=sa.Numeric(precision=12, scale=2),
        existing_nullable=False,
        server_default="1.00",
    )
