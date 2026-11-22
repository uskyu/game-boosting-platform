"""本地文件 → 对象存储（腾讯云 COS）迁移台账模型。

每一条记录对应 ``UPLOAD_DIR`` 下的一个文件（``rel_path`` 为相对该目录的
POSIX 路径，例如 ``orders/abc.jpg``）。

这张表不是「审计日志」，而是**可中断、可续传迁移的幂等账本**：

- ``PENDING``：已扫描入账、尚未成功上传；
- ``DONE``：该文件已上传到 COS 并通过 ``exists`` 校验。DONE 行一旦写入就
  视为已迁移，重复执行迁移会跳过它，因此重跑是安全的；
- ``FAILED``：最近一次上传失败，``error`` 保存中文原因、``attempts`` 记录
  已尝试次数；``attempts`` 达到上限后不再自动重试，避免坏文件无限重试。

清理本地文件时只允许删除「台账为 DONE 且 COS 中仍能验证到对象」的文件，
所以 DONE 行同时也是删除操作的唯一依据。应用按单 uvicorn 进程运行，
本表不承担跨进程锁的职责。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# 状态字面量：与迁移 076 中 VARCHAR(16) 的取值保持一致。
STATUS_PENDING = "PENDING"
STATUS_DONE = "DONE"
STATUS_FAILED = "FAILED"
# PURGED = 已迁移到 COS 且本地副本已被「清理」删除。必须单独记一个状态：
# 本地文件已不存在，若还留在 DONE 里，下一轮迁移会把它当"待迁移"再次选中，
# 然后报「找不到源文件」（2026-11-23 实测：清理 287 个后重跑迁移全部 FAILED）。
# PURGED 行不参与迁移，也不参与清理。
STATUS_PURGED = "PURGED"


class StorageMigration(Base):
    """单个本地文件的对象存储迁移状态（幂等账本的一行）。"""

    __tablename__ = "storage_migrations"
    __table_args__ = (
        UniqueConstraint("rel_path", name="uq_storage_migrations_rel_path"),
        Index("ix_storage_migrations_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # 相对 UPLOAD_DIR 的 POSIX 路径：唯一键保证「扫描 -> 入账」可重复执行。
    rel_path: Mapped[str] = mapped_column(String(500), nullable=False)

    # 迁移成功时记录的文件字节数；PENDING/FAILED 行为 NULL。
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # PENDING / DONE / FAILED（应用层只写这三个值）。
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATUS_PENDING, server_default=STATUS_PENDING
    )

    # 最近一次失败的中文原因；成功后不清空，便于管理员排查历史。
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 已尝试次数：达到上限（当前为 3）后迁移任务不再自动重试该行。
    attempts: Mapped[int] = mapped_column(
        Integer(), nullable=False, default=0, server_default="0"
    )

    # 迁移成功时间（naive UTC，与库内其他时间列一致）。
    migrated_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(), default=func.now(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<StorageMigration(id={self.id}, rel_path={self.rel_path!r}, "
            f"status={self.status}, attempts={self.attempts})>"
        )
