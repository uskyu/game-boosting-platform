"""Schemas for the self-service publish-permission workflow."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.models.publish_permission_request import PublishPermissionStatus
from app.schemas.serializers import serialize_datetime_utc


class PublishPermissionRequestItem(BaseModel):
    """一条发单权限申请（用户视角或后台视角共用）。"""

    id: int
    user_id: int
    username: str
    email: str
    user_created_at: datetime
    reason: str | None = None
    status: PublishPermissionStatus
    decision_reason: str | None = None
    reviewed_by: int | None = None
    reviewed_by_name: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None
    history_count: int = 0

    @field_serializer("user_created_at", "created_at", "resolved_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)


class PublishPermissionMineResponse(BaseModel):
    """「我的发单权限」页所需状态。"""

    can_publish: bool
    apply_enabled: bool
    latest: PublishPermissionRequestItem | None = None
    has_pending: bool = False


class PublishPermissionApplyRequest(BaseModel):
    """提交申请；申请理由可选，最多 500 字。"""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


class PublishPermissionReviewRequest(BaseModel):
    """管理员审核：approve 可直接通过，reject 必须填写原因。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=500)


class AdminPublishPermissionListResponse(BaseModel):
    items: list[PublishPermissionRequestItem]
    total: int
    page: int
    page_size: int
    pages: int
