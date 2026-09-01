"""Search schemas module."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.booster_service import BoosterServiceListResponse
from app.schemas.order import OrderListResponse


class SearchType(str, Enum):
    """Supported search targets."""

    ORDERS = "orders"
    SERVICES = "services"
    ALL = "all"


class SearchResponse(BaseModel):
    """Unified search response for orders and services."""

    q: str = Field(description="搜索关键词")
    type: SearchType = Field(description="搜索类型")
    orders: OrderListResponse | None = Field(default=None, description="订单搜索结果")
    services: BoosterServiceListResponse | None = Field(default=None, description="服务搜索结果")

    model_config = ConfigDict(from_attributes=True)
