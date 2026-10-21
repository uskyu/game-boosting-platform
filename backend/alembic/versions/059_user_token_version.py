"""users.token_version：改密码后作废既有登录会话

Revision ID: 059_user_token_version
Revises: 058_admin_recharge_records
Create Date: 2026-10-21 16:00:00.000000

老板实测问题（2026-10-21）：用户改完密码后，改密码之前登录的那一端**依然
在正常使用**，指望改密码把别人踢下线完全无效。

根因不在密码哈希，而在 JWT 本身：``users.hashed_password`` 变了只影响
「下一次用密码换令牌」，对已经发出去的 access/refresh 令牌没有任何约束力。
而 refresh 令牌每次都能再换一对新的，等于旧登录可以无限续期——密码改了
也永远停不下来。

本迁移加一个会话版本号：签发令牌时写入 ``ver`` 声明，所有校验点与
``users.token_version`` 比对，不一致即 401。改密码/管理员重置密码时自增，
此前的令牌一次性全部作废。

**为什么默认 0 是安全的**：改动前签发的令牌没有 ``ver`` 声明，校验侧按 0
处理，而现有所有用户的 token_version 也是 0，因此**部署本迁移不会把任何
已登录用户踢下线**，只有真正改了密码才触发失效。

只加一列且带 server_default，MySQL 8.2 走 INSTANT 路径，不重写表；用户表
只有 762 行，即使重写也是毫秒级。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "059_user_token_version"
down_revision: Union[str, None] = "058_admin_recharge_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "token_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="会话版本号：改密码时自增，作废此前的全部令牌",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
