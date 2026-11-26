"""管理员数据库容灾备份 API 的请求 / 响应模型。

数据库里的时间统一存 naive UTC；所有对外 datetime 必须经
``serialize_datetime_utc`` 输出带 ``Z`` 的字符串，否则浏览器会按本地时区
解读，产生 8 小时偏差。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.schemas.serializers import serialize_datetime_utc


class BackupRunOut(BaseModel):
    """一条备份执行记录。"""

    id: int
    status: str
    trigger: str
    object_key: str | None
    size_bytes: int | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    error_message: str | None
    include_notifications: bool

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("started_at", "finished_at")
    def serialize_datetime_fields(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)


class BackupSettingsUpdate(BaseModel):
    """保存备份设置；五个字段均为必填且不做局部更新。"""

    enabled: bool
    interval_minutes: int = Field(ge=5, le=1440)
    retention_count: int = Field(ge=1, le=500)
    # 真实规范化（去空白/斜杠、拒绝 .. 与空路径段）在端点里交给
    # db_backup_service.normalize_backup_prefix，以便返回中文 422。
    prefix: str
    include_notifications: bool

    model_config = ConfigDict(extra="forbid")


class BackupSettingsResponse(BaseModel):
    """GET/PUT /admin/backup/settings 的统一响应。"""

    enabled: bool
    interval_minutes: int
    retention_count: int
    prefix: str
    include_notifications: bool
    cos_configured: bool
    storage_mode: str
    # 正在执行、手动请求排队中或最近一条记录仍 RUNNING 时为 True。
    running: bool
    last_backup: BackupRunOut | None
    next_run_at: datetime | None

    @field_serializer("next_run_at")
    def serialize_next_run_at(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)


class BackupRunListResponse(BaseModel):
    items: list[BackupRunOut]
    total: int
    page: int
    page_size: int


class BackupRunRequestResponse(BaseModel):
    """POST /admin/backup/run：请求已排队，调度器会强制跑一次。"""

    requested: bool
    running: bool
