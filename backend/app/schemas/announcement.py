"""Schemas for administrator-managed announcements."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from app.models.announcement import AnnouncementFrequency
from app.schemas.serializers import serialize_datetime_utc


class AnnouncementInput(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content_html: str = Field(..., min_length=1, max_length=20000)
    frequency: AnnouncementFrequency = AnnouncementFrequency.DAILY
    is_enabled: bool = False
    priority: int = Field(default=0, ge=-1000, le=1000)
    start_at: datetime | None = None
    end_at: datetime | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_window(self):
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValueError("公告结束时间必须晚于开始时间")
        self.title = self.title.strip()
        if not self.title:
            raise ValueError("公告标题不能为空")
        if not self.content_html.strip():
            raise ValueError("公告内容不能为空")
        return self


class AnnouncementCreate(AnnouncementInput):
    pass


class AnnouncementUpdate(AnnouncementInput):
    pass


class AnnouncementPreviewRequest(BaseModel):
    content_html: str = Field(..., min_length=1, max_length=20000)

    model_config = ConfigDict(extra="forbid")


class AnnouncementPreviewResponse(BaseModel):
    safe_content_html: str


class AnnouncementPublicResponse(BaseModel):
    id: int
    title: str
    content_html: str
    frequency: AnnouncementFrequency


class AnnouncementAdminResponse(BaseModel):
    id: int
    title: str
    content_html: str
    safe_content_html: str
    frequency: AnnouncementFrequency
    is_enabled: bool
    priority: int
    start_at: datetime | None
    end_at: datetime | None
    created_by: int | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("start_at", "end_at", "created_at", "updated_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)

    model_config = ConfigDict(from_attributes=True)
