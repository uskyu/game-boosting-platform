"""全站发单权限总开关 + 用户明确封禁发单标记

Revision ID: 070_publish_permission_global
Revises: 069_publisher_blacklist
Create Date: 2026-11-12 10:00:00.000000

背景：
- ``site_settings.publish_permission_required`` 为全站总开关。true（默认）时
  非管理员需要已开通的发单权限（``users.can_publish``）才能发单；false 时
  全站开放，任何未被封禁发单的非管理员都可以发单。
- ``users.publish_banned`` 是明确的个人封禁标记，两种模式下都阻止发单，也
  阻止自助申请；它与 ``can_publish``（是否已开通）语义分离，管理员可以在
  用户管理里单独解除。

存量数据回填（关键）：旧版本把「从未开通」和「被封禁」都塞在
``can_publish=0`` 里，迁移时必须猜一次。回填规则：
- 读取 ``site_settings.registration_default_can_publish``（id=1）。这是新用户
  注册时是否默认开通发单权限的站点级默认值：生产站为 true，新站为 false。
- 行不存在（例如新库尚未创建 site_settings）按 true/生产站处理。
- 值为 true（生产站）：``can_publish=0`` 的非管理员视为被封禁发单，置
  ``publish_banned=1``；否则升级后在默认 required=true 下这些用户会被错误地
  视为「未开通」而无法继续使用（生产站不应出现大面积停发）。
- 值为 false（新站）：``can_publish=0`` 正是「未开通」的预期状态，保持
  ``publish_banned=0``；全站总开关打开时他们自然可以发单。
- 回填可能把个别生产站上「仅被管理员手动关闭发单」的用户误判为封禁。
  管理员可在后台用户管理中按 ``publish_banned`` 筛选并逐个解除，或直接
  「开通发单权限」——开通动作会同时清除封禁标记。

迁移为纯加法；不修改 ``can_publish`` 或其他业务数据。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "070_publish_permission_global"
down_revision: Union[str, None] = "069_publisher_blacklist"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "site_settings",
        sa.Column(
            "publish_permission_required",
            sa.Boolean(),
            nullable=False,
            server_default="1",
            comment="全站发单权限总开关：true=需要已开通发单权限，false=全站开放",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "publish_banned",
            sa.Boolean(),
            nullable=False,
            server_default="0",
            comment="是否被明确封禁发单：两种模式下都阻止发单与自助申请",
        ),
    )

    # 回填历史 can_publish=0 用户的封禁标记，规则见模块 docstring。
    bind = op.get_bind()
    row = bind.execute(
        sa.text(
            "SELECT registration_default_can_publish FROM site_settings WHERE id = 1"
        )
    ).first()
    # 缺少 site_settings 行时按生产站处理（true）：宁可让老用户先保持封禁，
    # 由管理员在用户管理里逐个开通，也不能把生产站的历史禁发用户全部放开。
    production_like = True if row is None else bool(row[0])
    if production_like:
        bind.execute(
            sa.text(
                "UPDATE users SET publish_banned = 1 "
                "WHERE role <> 'ADMIN' AND can_publish = 0"
            )
        )


def downgrade() -> None:
    # 先删用户表列，再删站点设置列；两列都是纯加法，回滚不恢复回填结果。
    op.drop_column("users", "publish_banned")
    op.drop_column("site_settings", "publish_permission_required")
