"""Review service module. Business logic for bidirectional reviews."""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order, OrderStatus
from app.models.review import Review
from app.models.user import User
from app.schemas.review import ReviewCreate, ReviewUpdate


class ReviewService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create_review(
        self, order_id: int, user: User, data: ReviewCreate
    ) -> Review:
        """Create a review on a completed order."""
        order = await self._get_completed_order(order_id)
        target_id = self._resolve_target(order, user)

        # Check duplicate
        existing = await self._db.execute(
            select(Review).where(
                Review.order_id == order_id,
                Review.reviewer_id == user.id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="你已经评价过这个订单了",
            )

        review = Review(
            order_id=order_id,
            reviewer_id=user.id,
            target_id=target_id,
            rating=data.rating,
            content=data.content,
        )
        self._db.add(review)
        await self._db.flush()
        await self._db.refresh(review)
        return review

    async def update_review(
        self, order_id: int, user: User, data: ReviewUpdate
    ) -> Review:
        """Update own review."""
        result = await self._db.execute(
            select(Review).where(
                Review.order_id == order_id,
                Review.reviewer_id == user.id,
            )
        )
        review = result.scalar_one_or_none()
        if review is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="未找到你的评价",
            )

        if data.rating is not None:
            review.rating = data.rating
        if data.content is not None:
            review.content = data.content
        review.updated_at = datetime.now(timezone.utc)

        await self._db.flush()
        await self._db.refresh(review)
        return review

    async def get_order_reviews(self, order_id: int) -> list[Review]:
        """Get all reviews for an order."""
        result = await self._db.execute(
            select(Review)
            .where(Review.order_id == order_id)
            .order_by(Review.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_user_reviews(self, user_id: int) -> tuple[list[Review], float | None]:
        """Get reviews received by a user, with average rating."""
        result = await self._db.execute(
            select(Review)
            .where(Review.target_id == user_id)
            .order_by(Review.created_at.desc())
        )
        reviews = list(result.scalars().all())

        avg_result = await self._db.execute(
            select(func.avg(Review.rating)).where(Review.target_id == user_id)
        )
        avg = avg_result.scalar()
        average_rating = round(float(avg), 1) if avg is not None else None

        return reviews, average_rating

    async def _get_completed_order(self, order_id: int) -> Order:
        result = await self._db.execute(
            select(Order).where(Order.id == order_id)
        )
        order = result.scalar_one_or_none()
        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="订单不存在",
            )
        if order.status != OrderStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只有已完成的订单才能评价",
            )
        return order

    def _resolve_target(self, order: Order, user: User) -> int:
        """Determine who the review target is based on reviewer role."""
        if user.id == order.user_id:
            if order.booster_id is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="该订单没有代练，无法评价",
                )
            return order.booster_id
        elif user.id == order.booster_id:
            return order.user_id
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只有订单参与者才能评价",
            )


def get_review_service(db: AsyncSession) -> ReviewService:
    return ReviewService(db)
