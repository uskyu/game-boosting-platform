"""Review schemas for request/response validation."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.order import UserBrief


class ReviewCreate(BaseModel):
    """Schema for creating a review."""

    rating: int = Field(..., ge=1, le=5, description="评分 (1-5)")
    content: str | None = Field(default=None, max_length=1000, description="评价内容")


class ReviewUpdate(BaseModel):
    """Schema for updating a review."""

    rating: int | None = Field(default=None, ge=1, le=5, description="评分 (1-5)")
    content: str | None = Field(default=None, max_length=1000, description="评价内容")


class ReviewResponse(BaseModel):
    """Review response schema."""

    id: int
    order_id: int
    reviewer_id: int
    target_id: int
    rating: int
    content: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
    reviewer: UserBrief | None = None

    model_config = ConfigDict(from_attributes=True)


class ReviewListResponse(BaseModel):
    """List of reviews with average rating."""

    items: list[ReviewResponse]
    total: int
    average_rating: float | None = None
