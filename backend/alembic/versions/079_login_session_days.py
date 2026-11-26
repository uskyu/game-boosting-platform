"""site_settings 增加 login_session_days：后台可调登录保持天数

Revision ID: 079_login_session_days
Revises: 078_notification_sounds
Create Date: 2026-11-26 00:00:00.000000

背景：老板口中的「Cookie 登录时间」原先写死在
``settings.REFRESH_TOKEN_EXPIRE_DAYS``（默认 7 天），后台不可调。新增
``site_settings.login_session_days`` 后，登录/注册/改密码/刷新令牌都按该值
签发；修改只影响之后建立的登录会话，已有会话保持登录时的到期时间。

本迁移是纯加法迁移：nullable=False + server_default="7"，存量行自动获得
默认值，不需要回填，也不改变任何现有会话的 expires_at。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "079_login_session_days"
down_revision: str | None = "078_notification_sounds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "site_settings",
        sa.Column(
            "login_session_days",
            sa.Integer(),
            nullable=False,
            server_default="7",
            comment="登录保持天数（刷新令牌/设备会话有效期），1-365 天",
        ),
    )


def downgrade() -> None:
    op.drop_column("site_settings", "login_session_days")
