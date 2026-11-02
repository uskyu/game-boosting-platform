"""打手排行榜（按接单金额）响应结构。"""

from typing import Literal

from pydantic import BaseModel, Field


class LeaderboardEntry(BaseModel):
    """排行榜中的一名打手。"""

    rank: int = Field(description="名次（从 1 开始）")
    user_id: int = Field(description="打手用户ID")
    username: str = Field(description="打手用户名")
    avatar_url: str | None = Field(default=None, description="头像 URL")
    amount: float = Field(description="周期内接单金额（元）")


class LeaderboardMe(BaseModel):
    """当前用户在本期排行榜中的位置。"""

    rank: int | None = Field(
        default=None,
        description="当前用户名次；本期未接单或不在榜单口径内时为 null",
    )
    amount: float = Field(default=0.0, description="当前用户周期内接单金额")
    gap_to_previous: float | None = Field(
        default=None,
        description="距离上一名还差多少金额；第 1 名或未上榜时为 null",
    )


class LeaderboardResponse(BaseModel):
    """排行榜接口响应。"""

    period: Literal["day", "week", "all"] = Field(description="榜单周期")
    items: list[LeaderboardEntry] = Field(description="前 30 名")
    me: LeaderboardMe = Field(description="当前用户排名信息")
