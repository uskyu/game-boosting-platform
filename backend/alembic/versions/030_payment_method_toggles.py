"""Payment settings: drop the manual callback address and JSON pay methods.

管理员不再填写回调地址（改为按充值请求来源自动推导），支付方式由
JSON 文本改为两个开关：``alipay_enabled`` / ``wxpay_enabled``。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "030_payment_method_toggles"
down_revision: Union[str, None] = "029_epay_recharge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("payment_settings", "notify_base_url")
    op.drop_column("payment_settings", "pay_methods")
    op.add_column(
        "payment_settings",
        sa.Column("alipay_enabled", sa.Boolean(), nullable=False, server_default="1"),
    )
    op.add_column(
        "payment_settings",
        sa.Column("wxpay_enabled", sa.Boolean(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("payment_settings", "wxpay_enabled")
    op.drop_column("payment_settings", "alipay_enabled")
    op.add_column(
        "payment_settings", sa.Column("pay_methods", sa.Text(), nullable=True)
    )
    op.add_column(
        "payment_settings", sa.Column("notify_base_url", sa.String(500), nullable=True)
    )
    # 还原原先的默认支付方式文本
    op.execute(
        "UPDATE payment_settings SET pay_methods = "
        "'[{\"name\": \"支付宝\", \"type\": \"alipay\"}, {\"name\": \"微信\", \"type\": \"wxpay\"}]' "
        "WHERE id = 1"
    )
