"""发单人黑名单请求/响应 Schema。

接口契约（2026-11-11 冻结）：
- 列表项只带 id / blocked_user_id / username / avatar_url / reason / created_at，
  **不回显邮箱**；
- lookup 只返回 id / username / avatar_url，最多 1 人。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.schemas.serializers import serialize_datetime_utc


class PublisherBlacklistCreate(BaseModel):
    """拉黑请求体：目标用户来自 lookup 或订单接单名单。"""

    user_id: int = Field(ge=1, description="要拉黑的用户ID")
    reason: str | None = Field(
        default=None,
        max_length=200,
        description="可选备注（仅发单人/管理员可见，不对被拉黑用户展示）",
    )


class PublisherBlacklistItem(BaseModel):
    """我的黑名单列表项（发单人视角）。"""

    id: int = Field(description="黑名单记录ID")
    blocked_user_id: int = Field(description="被拉黑用户ID")
    username: str = Field(description="被拉黑用户名")
    avatar_url: str | None = Field(default=None, description="被拉黑用户头像")
    reason: str | None = Field(default=None, description="拉黑备注")
    created_at: datetime = Field(description="拉黑时间")

    @field_serializer("created_at")
    def serialize_blacklist_datetime(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)

    model_config = ConfigDict(from_attributes=True)


class PublisherBlacklistListResponse(BaseModel):
    """分页黑名单列表。"""

    items: list[PublisherBlacklistItem] = Field(description="黑名单列表")
    total: int = Field(description="总数量")
    page: int = Field(description="当前页码")
    page_size: int = Field(description="每页数量")
    pages: int = Field(description="总页数")


class PublisherBlacklistLookupResponse(BaseModel):
    """精确查找结果：最多返回 1 人，不回显邮箱。"""

    id: int = Field(description="用户ID")
    username: str = Field(description="用户名")
    avatar_url: str | None = Field(default=None, description="用户头像")

    model_config = ConfigDict(from_attributes=True)
