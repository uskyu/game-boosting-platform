"""对象存储（腾讯云 COS）配置与迁移台账

Revision ID: 076_object_storage
Revises: 075_hall_sort_mode
Create Date: 2026-11-22 00:00:00.000000

背景：上传文件原先全部落在本地 ``UPLOAD_DIR``，单机磁盘容量与跨机读取都
成问题。本迁移是纯加法迁移，不改变任何现有行为：

1. ``site_settings`` 增加对象存储开关与 COS 凭据列。默认
   ``storage_mode='LOCAL'``，与迁移前完全一致；``cos_secret_id`` /
   ``cos_secret_key`` 存的是 ``encrypt_text`` 的 Fernet 密文，任何接口
   都不会下发明文。
2. 新建 ``storage_migrations`` 台账表：记录每个本地文件的迁移状态
   （PENDING/DONE/FAILED）、失败原因与尝试次数。``rel_path`` 唯一键保证
   「扫描-入账」可重复执行；DONE 行即可中断、可续传迁移的幂等账本。

不建反向数据：``storage_migrations`` 只由应用层写入，历史文件在首次
点击「开始迁移」时由扫描任务入账，不需要在迁移脚本里遍历磁盘。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "076_object_storage"
down_revision: str | None = "075_hall_sort_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    "cos_cdn_domain",
    "cos_region",
    "cos_bucket",
    "cos_secret_key",
    "cos_secret_id",
    "storage_mode",
)


def upgrade() -> None:
    op.add_column(
        "site_settings",
        sa.Column(
            "storage_mode",
            sa.String(length=16),
            nullable=False,
            server_default="LOCAL",
            comment="文件存储模式：LOCAL=本地磁盘（默认），COS=腾讯云对象存储",
        ),
    )
    op.add_column(
        "site_settings",
        sa.Column(
            "cos_secret_id",
            sa.Text(),
            nullable=True,
            comment="COS SecretId（encrypt_text 密文，仅服务端解密使用）",
        ),
    )
    op.add_column(
        "site_settings",
        sa.Column(
            "cos_secret_key",
            sa.Text(),
            nullable=True,
            comment="COS SecretKey（encrypt_text 密文，仅服务端解密使用）",
        ),
    )
    op.add_column(
        "site_settings",
        sa.Column(
            "cos_bucket",
            sa.String(length=100),
            nullable=True,
            comment="COS 存储桶名称，例如 game-1314124236",
        ),
    )
    op.add_column(
        "site_settings",
        sa.Column(
            "cos_region",
            sa.String(length=50),
            nullable=True,
            comment="COS 地域，例如 ap-chongqing",
        ),
    )
    op.add_column(
        "site_settings",
        sa.Column(
            "cos_cdn_domain",
            sa.String(length=200),
            nullable=True,
            comment="可选的自定义 CDN 域名（不含协议，留空则按默认域名读取）",
        ),
    )

    op.create_table(
        "storage_migrations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "rel_path",
            sa.String(length=500),
            nullable=False,
            comment="相对 UPLOAD_DIR 的 POSIX 路径，例如 orders/abc.jpg",
        ),
        sa.Column(
            "size_bytes",
            sa.BigInteger(),
            nullable=True,
            comment="迁移成功时记录的文件字节数",
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="PENDING",
            comment="PENDING/DONE/FAILED",
        ),
        sa.Column("error", sa.Text(), nullable=True, comment="最近一次失败原因"),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="已尝试次数；达到上限后不再自动重试",
        ),
        sa.Column("migrated_at", sa.DateTime(), nullable=True, comment="迁移成功时间"),
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
        sa.UniqueConstraint("rel_path", name="uq_storage_migrations_rel_path"),
    )
    op.create_index(
        "ix_storage_migrations_status", "storage_migrations", ["status"], unique=False
    )


def downgrade() -> None:
    # 台账表先删；COS 配置列按与 upgrade 相反的顺序移除。
    op.drop_index("ix_storage_migrations_status", table_name="storage_migrations")
    op.drop_table("storage_migrations")
    for column in _COLUMNS:
        op.drop_column("site_settings", column)
