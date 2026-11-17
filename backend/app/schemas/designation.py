"""指定派单候选人请求/响应 Schema。

对外只暴露接单决策需要的字段：
- 候选人不回显登录邮箱（仅用户名/头像/保证金/接单状态/被指定次数）；
- ``recent`` 为当前发布人最近 90 天指定过的打手，``results`` 为关键词搜索
  结果；两者结构一致，前端可复用同一卡片组件。
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class DesignationCandidate(BaseModel):
    """一个可选打手候选。"""

    id: int = Field(description="打手用户ID")
    username: str = Field(description="打手昵称")
    avatar_url: str | None = Field(default=None, description="头像URL")
    deposit_balance: Decimal = Field(
        default=Decimal("0.00"),
        description="保证金余额（无钱包记录时为 0）",
    )
    can_accept: bool = Field(description="当前是否允许接单；false 时前端标记「已停接」并禁止选择")
    selected_times: int = Field(
        default=0,
        description="当前发布人最近 90 天内指定该打手的次数",
    )
    is_self: bool = Field(
        default=False,
        description="是否为当前发布人自己；true 时前端标记「本人」并禁止选择",
    )
    is_blacklisted: bool = Field(
        default=False,
        description="是否被当前发布人拉黑；true 时前端标记「已拉黑」并提示先解除",
    )


class DesignationCandidatesResponse(BaseModel):
    """指定派单候选人响应：最近指定 + 关键词搜索结果。"""

    query: str = Field(description="规范化后的搜索关键词；最近列表为空串")
    recent: list[DesignationCandidate] = Field(
        default_factory=list,
        description="最近指定的打手（按指定次数、最近指定时间倒序，最多 6 人）",
    )
    results: list[DesignationCandidate] = Field(
        default_factory=list,
        description="关键词搜索命中的打手（最多 10 人）",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query": "",
                "recent": [
                    {
                        "id": 7,
                        "username": "打手小李",
                        "avatar_url": None,
                        "deposit_balance": "500.00",
                        "can_accept": True,
                        "selected_times": 3,
                        "is_self": False,
                        "is_blacklisted": False,
                    }
                ],
                "results": [],
            }
        }
    )
