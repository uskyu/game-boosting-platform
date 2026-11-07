"""site_settings 增加注册默认权限列 + 发单权限申请表

Revision ID: 068_publish_permission
Revises: 067_site_footer_html
Create Date: 2026-11-07 10:00:00.000000

一套代码服务两台站点：
- 生产站沿用默认值（新用户默认可发单/可接单），行为不变；
- 新站首次部署通过 env（REGISTRATION_DEFAULT_CAN_PUBLISH=false）初始化
  单行设置，新注册用户默认只能接单，发单需管理员开通或自助申请审核。

三列都带 server_default，存量 site_settings 行自动取 1（不改变生产站行为）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "068_publish_permission"
down_revision: Union[str, None] = "067_site_footer_html"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

publish_permission_status_enum = sa.Enum(
    "PENDING",
    "APPROVED",
    "REJECTED",
    "WITHDRAWN",
    name="publish_permission_status_enum",
)


def upgrade() -> None:
    op.add_column(
        "site_settings",
        sa.Column(
            "registration_default_can_publish",
            sa.Boolean(),
            nullable=False,
            server_default="1",
            comment="新注册用户默认是否有发单权限",
        ),
    )
    op.add_column(
        "site_settings",
        sa.Column(
            "registration_default_can_accept",
            sa.Boolean(),
            nullable=False,
            server_default="1",
            comment="新注册用户默认是否有接单权限",
        ),
    )
    op.add_column(
        "site_settings",
        sa.Column(
            "publish_permission_apply_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="1",
            comment="是否允许无发单权限用户自助申请发单权限",
        ),
    )

    publish_permission_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "publish_permission_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("status", publish_permission_status_enum, nullable=False, server_default="PENDING"),
        sa.Column("decision_reason", sa.String(length=500), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_publish_permission_requests_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_publish_permission_requests_reviewed_by_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_publish_permission_requests"),
    )
    op.create_index(
        "ix_publish_permission_requests_user_id",
        "publish_permission_requests",
        ["user_id"],
    )
    op.create_index(
        "ix_publish_permission_requests_status",
        "publish_permission_requests",
        ["status"],
    )


def downgrade() -> None:
    # drop_table 会一并删除索引与外键；先单独删 user_id 索引会被 MySQL
    # 以外键仍依赖该索引（1553）拒绝。
    op.drop_table("publish_permission_requests")
    publish_permission_status_enum.drop(op.get_bind(), checkfirst=True)

    op.drop_column("site_settings", "publish_permission_apply_enabled")
    op.drop_column("site_settings", "registration_default_can_accept")
    op.drop_column("site_settings", "registration_default_can_publish")

