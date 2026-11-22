"""signed_url_cache：COS 预签名 URL 的复用台账

Revision ID: 077_signed_url_cache
Revises: 076_object_storage
Create Date: 2026-11-22 00:00:00.000000

背景：COS 切私有读之后，图片必须由服务端签发 URL 才能被浏览器直连拉取。
``qcloud_cos`` 的 ``get_presigned_url`` 每次调用都会重新生成 ``q-sign-time``，
即使 (key, 过期时刻) 完全相同，URL 字符串也不一样；浏览器据此会把「同一张
图」当成不同资源反复下载，HTTP 缓存彻底失效。

因此签名必须「按 (object_key, expire_at) 复用」：本表持久化已经签发过的
完整 URL，同一 7 天窗口内重复请求直接复用同一串 URL，页面刷新/大厅轮询
都不会产生新签名。``expire_at`` 是 COS 签名的到期瞬间（naive UTC），
``url`` 里只有 AKID（SecretId）与签名参数，**绝不含 SecretKey**。

纯加法迁移，默认不影响任何现有行为：LOCAL 模式不会写这张表。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "077_signed_url_cache"
down_revision: str | None = "076_object_storage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signed_url_cache",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "object_key",
            sa.String(length=500),
            nullable=False,
            comment="相对 UPLOAD_DIR 的对象 key，例如 orders/abc.jpg",
        ),
        sa.Column(
            "expire_at",
            sa.DateTime(),
            nullable=False,
            comment="COS 签名到期瞬间（naive UTC），按 7 天窗口对齐",
        ),
        sa.Column(
            "url",
            sa.Text(),
            nullable=False,
            comment="已签发的完整 URL（只含 AKID 与签名参数，不含 SecretKey）",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        # 同一 key 的同一到期窗口只允许一行：重复签发直接复用该行。
        sa.UniqueConstraint(
            "object_key", "expire_at", name="uq_signed_url_cache_key_expire"
        ),
    )
    # 清理过期行（只在到期窗口翻页后才会产生新行）需要按 expire_at 扫。
    op.create_index(
        "ix_signed_url_cache_expire", "signed_url_cache", ["expire_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_signed_url_cache_expire", table_name="signed_url_cache")
    op.drop_table("signed_url_cache")
