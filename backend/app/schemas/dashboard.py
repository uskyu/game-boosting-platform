"""Admin dashboard schemas for data visualization."""


from pydantic import BaseModel, Field


class OverviewStats(BaseModel):
    """Platform-wide overview numbers."""

    total_users: int = Field(description="总用户数")
    total_boosters: int = Field(description="代练总数")
    total_orders: int = Field(description="订单总数")
    total_revenue: float = Field(description="总收入(已完成)")
    pending_orders: int = Field(description="待接单数")
    active_orders: int = Field(description="进行中订单")
    completed_orders: int = Field(description="已完成订单")
    disputed_orders: int = Field(description="争议订单")


class TrendPoint(BaseModel):
    """A single data point on a time-series chart."""

    date: str = Field(description="日期标签")
    count: int = Field(default=0, description="数量")
    revenue: float = Field(default=0.0, description="金额")


class OrderTrendResponse(BaseModel):
    """Order trend data over time."""

    points: list[TrendPoint] = Field(description="趋势数据点")
    period: str = Field(description="周期: day/week/month")


class GameDistributionItem(BaseModel):
    """Game distribution for pie chart."""

    game_name: str = Field(description="游戏名")
    count: int = Field(description="订单数")
    revenue: float = Field(description="收入")


class GameDistributionResponse(BaseModel):
    """Game distribution data."""

    items: list[GameDistributionItem] = Field(description="分布数据")


class BoosterRankItem(BaseModel):
    """Booster ranking entry."""

    user_id: int = Field(description="用户ID")
    username: str = Field(description="用户名")
    avatar_url: str | None = Field(default=None, description="头像")
    credit_score: int = Field(description="信誉分")
    credit_level: str = Field(description="信誉等级")
    total_completed: int = Field(description="完成数")
    avg_rating: float = Field(description="平均评分")
    total_revenue: float = Field(description="总收入")


class BoosterRankingResponse(BaseModel):
    """Top boosters ranking."""

    items: list[BoosterRankItem] = Field(description="排行榜")


class UserGrowthPoint(BaseModel):
    """User growth data point."""

    date: str = Field(description="日期标签")
    new_users: int = Field(default=0, description="新增用户")
    cumulative: int = Field(default=0, description="累计用户")


class UserGrowthResponse(BaseModel):
    """User growth trend data."""

    points: list[UserGrowthPoint] = Field(description="趋势数据点")
