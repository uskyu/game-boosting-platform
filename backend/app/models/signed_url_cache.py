"""COS 预签名 URL 的复用台账模型。

一行 = 某个对象 key 在一个 7 天签名窗口内签发过的完整 URL。

为什么要持久化：COS 签名包含 ``q-sign-time``（实际签发时刻），所以同样
的 (key, expire_at) 每次调用都会得到不同的字符串。浏览器按完整 URL 做
HTTP 缓存键，只要字符串变了就得重新下载。把首次签发的 URL 存下来、在
到期前 1 小时内原样复用，才能让「同一张图」在整个 7 天窗口内命中缓存。

安全口径：``url`` 只含 AKID（SecretId）与签名参数，**绝不含 SecretKey**；
表内不存任何明文凭据，凭据只以密文形式留在 ``site_settings``。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SignedUrlCache(Base):
    """某个对象 key 在某个到期窗口内签发过的预签名 URL。"""

    __tablename__ = "signed_url_cache"
    __table_args__ = (
        # 同一 key 的同一到期窗口只允许一行，重复签发直接复用。
        UniqueConstraint(
            "object_key", "expire_at", name="uq_signed_url_cache_key_expire"
        ),
        # 清理过期行按 expire_at 扫。
        Index("ix_signed_url_cache_expire", "expire_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # 相对 UPLOAD_DIR 的对象 key（与 storage_service.normalize_key 同一口径），
    # 例如 orders/abc.jpg、chat/def.png、withdrawals/7/qr.png。
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)

    # COS 签名到期瞬间：naive UTC，按 7 天窗口对齐（见 url_signing.next_expire_at）。
    expire_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)

    # 完整签名 URL（含 q-ak=AKID... 与签名参数；不含 SecretKey）。
    url: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(), default=func.now(), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<SignedUrlCache(object_key={self.object_key!r}, "
            f"expire_at={self.expire_at!r})>"
        )
