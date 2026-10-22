"""site_settings: 注册/接单验证码开关 + 同时在线设备上限

Revision ID: 060_security_settings
Revises: 059_user_token_version
Create Date: 2026-10-22 19:00:00.000000

老板 2026-10-22 拍板：
- 注册验证码默认开启，后台可关闭；
- 接单验证码默认关闭，后台可开启；
- 同一账号同时在线设备数可配，0 表示不限制，默认 2。

三列都带 server_default，存量行自动取默认值；site_settings 只有一行，
加列开销可忽略。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "060_security_settings"
down_revision: Union[str, None] = "059_user_token_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "site_settings",
        sa.Column(
            "registration_captcha_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="1",
            comment="注册时是否要求图形验证码",
        ),
    )
    op.add_column(
        "site_settings",
        sa.Column(
            "accept_captcha_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="0",
            comment="接单时是否要求图形验证码",
        ),
    )
    op.add_column(
        "site_settings",
        sa.Column(
            "max_online_devices",
            sa.Integer(),
            nullable=False,
            server_default="2",
            comment="同一账号最多同时在线设备数，0=不限制",
        ),
    )


def downgrade() -> None:
    op.drop_column("site_settings", "max_online_devices")
    op.drop_column("site_settings", "accept_captcha_enabled")
    op.drop_column("site_settings", "registration_captcha_enabled")
