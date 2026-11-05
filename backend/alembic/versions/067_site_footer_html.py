"""site_settings 增加 footer_html：页脚底部自定义 HTML（备案号）

Revision ID: 067_site_footer_html
Revises: 066_payment_gateways
Create Date: 2026-11-06 09:30:00.000000

后台「站点管理」新增一个可编辑的 HTML 字段，前台渲染在整页最底部，
默认留空（不显示）。主要用于填 ICP 备案号及工信部备案链接。

默认值刻意留空而不是预置一段文本：展示未备案/错误的备案号属于合规风险，
必须由管理员填入真实号码后才会出现。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "067_site_footer_html"
down_revision: Union[str, None] = "066_payment_gateways"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "site_settings",
        sa.Column(
            "footer_html",
            sa.Text(),
            nullable=True,
            comment="页脚底部自定义 HTML（备案号等）；NULL=不显示",
        ),
    )


def downgrade() -> None:
    op.drop_column("site_settings", "footer_html")
