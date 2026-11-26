"""数据库容灾备份执行记录。

每次备份（定时或管理员手动触发）写一行：开始插入 ``RUNNING``，结束后改成
``SUCCESS`` / ``FAILED``。成功行的 ``object_key`` 指向 COS 上的
``<prefix>/mysql-YYYYmmdd-HHMMSS.sql.gz``，保留清理只按这些成功行工作。

时间列统一存 naive UTC，与库内其他 DateTime 列保持一致。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# 状态与触发来源字面量：API 层与调度器共用，避免各处散落字符串。
STATUS_RUNNING = "RUNNING"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"

TRIGGER_SCHEDULED = "SCHEDULED"
TRIGGER_MANUAL = "MANUAL"


class DatabaseBackup(Base):
    """一次数据库备份任务的执行记录。"""

    __tablename__ = "database_backups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # RUNNING / SUCCESS / FAILED
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=STATUS_RUNNING,
        server_default=STATUS_RUNNING,
        index=True,
    )

    # SCHEDULED=后台定时触发；MANUAL=管理员手动请求
    trigger: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=TRIGGER_SCHEDULED,
        server_default=TRIGGER_SCHEDULED,
    )

    # 成功上传后的 COS 对象 key；失败的备份没有对象。
    object_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # 开始时间（naive UTC）；失败重试间隔与保留排序都以它为基准。
    started_at: Mapped[datetime] = mapped_column(
        DateTime(),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        index=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer(), nullable=True)

    # 失败原因（已截断的中文单行文本），不包含数据库密码或 COS 密钥。
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 本次备份是否包含 notifications 表数据（结构始终会导出）。
    include_notifications: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=False, server_default="0"
    )

    # 手动触发人；定时任务为 NULL。用户被删除后保留记录并置空。
    triggered_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<DatabaseBackup(id={self.id}, status={self.status}, "
            f"trigger={self.trigger}, started_at={self.started_at})>"
        )
