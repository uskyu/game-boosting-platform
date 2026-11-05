"""payment_gateways: 支付网关改为可配置多套，管理员自选当前启用的一家

Revision ID: 066_payment_gateways
Revises: 065_order_title_replacements
Create Date: 2026-11-05 22:10:00.000000

1. 新建 payment_gateways：每家支付服务商一行，地址/商户号/密钥/查询地址
   都在行内，``append_submit_php`` 决定提交地址要不要补 ``/submit.php``。
2. 把 payment_settings 里现有的单套配置播种成第一行（并设为当前启用），
   保证升级后行为与升级前完全一致。
3. recharge_orders 增加 gateway_id / gateway_name：历史订单回填到这一行，
   回调按订单的网关验签，之后切换网关不会影响已创建的订单。

payment_settings 里的 pay_address / epay_id / epay_key 三列暂时保留：
旧版本前端在滚动发布期间仍会读取它们，下一版再删。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "066_payment_gateways"
down_revision: Union[str, None] = "065_order_title_replacements"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payment_gateways",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("submit_url", sa.String(length=500), nullable=False),
        sa.Column(
            "append_submit_php",
            sa.Boolean(),
            nullable=False,
            server_default="1",
            comment="提交地址是否自动补 /submit.php",
        ),
        sa.Column("merchant_id", sa.String(length=64), nullable=False),
        sa.Column("secret_key", sa.String(length=255), nullable=False),
        sa.Column("query_url", sa.String(length=500), nullable=True),
        sa.Column(
            "alipay_enabled", sa.Boolean(), nullable=False, server_default="1"
        ),
        sa.Column("wxpay_enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payment_gateway_active", "payment_gateways", ["is_active"])
    op.create_index("ix_payment_gateway_enabled", "payment_gateways", ["enabled"])

    # 把现有单套配置播种成第一行；查询地址按易支付标准推导为 {基地址}/api.php
    op.execute(
        """
        INSERT INTO payment_gateways
            (name, submit_url, append_submit_php, merchant_id, secret_key,
             query_url, alipay_enabled, wxpay_enabled, enabled, is_active)
        SELECT
            '默认易支付',
            TRIM(TRAILING '/' FROM pay_address),
            1,
            epay_id,
            epay_key,
            CONCAT(TRIM(TRAILING '/' FROM pay_address), '/api.php'),
            alipay_enabled,
            wxpay_enabled,
            enabled,
            1
        FROM payment_settings
        WHERE id = 1
          AND pay_address IS NOT NULL AND pay_address <> ''
          AND epay_id IS NOT NULL AND epay_id <> ''
          AND epay_key IS NOT NULL AND epay_key <> ''
        """
    )

    op.add_column(
        "recharge_orders", sa.Column("gateway_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "recharge_orders", sa.Column("gateway_name", sa.String(length=50), nullable=True)
    )
    op.create_index(
        "ix_recharge_orders_gateway_id", "recharge_orders", ["gateway_id"]
    )
    op.create_foreign_key(
        "fk_recharge_orders_gateway_id",
        "recharge_orders",
        "payment_gateways",
        ["gateway_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 历史订单归到刚播种的网关：它们本来就是用那套配置下的单
    op.execute(
        """
        UPDATE recharge_orders
        SET gateway_id = (SELECT MIN(id) FROM payment_gateways),
            gateway_name = '默认易支付'
        WHERE gateway_id IS NULL
          AND EXISTS (SELECT 1 FROM payment_gateways)
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_recharge_orders_gateway_id", "recharge_orders", type_="foreignkey"
    )
    op.drop_index("ix_recharge_orders_gateway_id", table_name="recharge_orders")
    op.drop_column("recharge_orders", "gateway_name")
    op.drop_column("recharge_orders", "gateway_id")
    op.drop_index("ix_payment_gateway_enabled", table_name="payment_gateways")
    op.drop_index("ix_payment_gateway_active", table_name="payment_gateways")
    op.drop_table("payment_gateways")
