"""
Credit / reputation calculation service.
Recalculates a booster's credit score, level, and badge tags.
"""

import logging
import math

from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order, OrderStatus
from app.models.review import Review
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)

# Credit level thresholds
CREDIT_LEVELS = [
    (90, "master", "大师代练"),
    (80, "diamond", "钻石代练"),
    (60, "gold", "黄金代练"),
    (40, "silver", "白银代练"),
    (0, "bronze", "青铜代练"),
]

# Quota limits per level
LEVEL_QUOTA = {
    "master": 50,
    "diamond": 20,
    "gold": 10,
    "silver": 5,
    "bronze": 2,
}


def compute_credit_level(score: int) -> tuple[str, str]:
    """Return (level_key, display_name) for a given credit score."""
    for threshold, key, name in CREDIT_LEVELS:
        if score >= threshold:
            return key, name
    return "bronze", "青铜代练"


def compute_badge_tags(
    *,
    avg_rating: float,
    total_completed: int,
    completion_rate: float,
    avg_response_minutes: int,
    credit_level: str,
) -> list[str]:
    """Derive badge tags from stats."""
    tags: list[str] = []
    if avg_response_minutes > 0 and avg_response_minutes <= 5:
        tags.append("极速响应")
    if avg_rating >= 4.8 and total_completed >= 10:
        tags.append("好评如潮")
    if completion_rate >= 98.0 and total_completed >= 20:
        tags.append("使命必达")
    if total_completed >= 100:
        tags.append("百单老手")
    elif total_completed >= 50:
        tags.append("经验丰富")
    if credit_level == "master":
        tags.append("大师认证")
    return tags


class CreditService:
    """Recalculates booster reputation metrics."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def recalculate(self, booster_id: int) -> User | None:
        """Full recalculation for one booster. Returns updated User or None."""
        result = await self._db.execute(
            select(User).where(User.id == booster_id)
        )
        user = result.scalar_one_or_none()
        if user is None or user.role not in (UserRole.BOOSTER, UserRole.ADMIN):
            return None

        # Count orders by status
        status_counts = await self._db.execute(
            select(Order.status, func.count(Order.id))
            .where(Order.booster_id == booster_id)
            .group_by(Order.status)
        )
        counts: dict[str, int] = {}
        for row in status_counts:
            counts[row[0].value if hasattr(row[0], "value") else row[0]] = row[1]

        total_completed = counts.get(OrderStatus.COMPLETED.value, 0)
        total_disputed = counts.get(OrderStatus.DISPUTED.value, 0)
        total_handled = (
            total_completed
            + total_disputed
            + counts.get(OrderStatus.CANCELLED.value, 0)
            + counts.get(OrderStatus.DELIVERED.value, 0)
        )

        completion_rate = (
            round(total_completed / total_handled * 100, 2) if total_handled > 0 else 0.0
        )

        # Average rating from reviews where this user is the target
        avg_result = await self._db.execute(
            select(func.avg(Review.rating)).where(Review.target_id == booster_id)
        )
        avg_rating = float(avg_result.scalar() or 0.0)
        avg_rating = round(avg_rating, 2)

        # Average response time (locked_at - created_at for orders this booster accepted)
        response_result = await self._db.execute(
            select(
                func.avg(
                    func.timestampdiff(
                        literal_column("MINUTE"), Order.created_at, Order.locked_at
                    )
                )
            ).where(
                Order.booster_id == booster_id,
                Order.locked_at.is_not(None),
            )
        )
        avg_response_raw = response_result.scalar()
        avg_response_minutes = int(avg_response_raw) if avg_response_raw else 0

        # Calculate credit score
        score = 100  # base

        # +2 per completed order (cap +40)
        score += min(total_completed * 2, 40)

        # +3 per 5-star review
        five_star_result = await self._db.execute(
            select(func.count(Review.id)).where(
                Review.target_id == booster_id,
                Review.rating == 5,
            )
        )
        five_star_count = five_star_result.scalar() or 0
        score += five_star_count * 3

        # -5 per dispute
        score -= total_disputed * 5

        # Bonus for high completion rate
        if completion_rate >= 98 and total_completed >= 10:
            score += 5

        # Bonus for fast response
        if 0 < avg_response_minutes <= 5:
            score += 5

        # Logarithmic bonus for volume
        if total_completed > 0:
            score += int(math.log2(total_completed + 1)) * 2

        # Clamp to 0-100
        score = max(0, min(100, score))

        level_key, _level_name = compute_credit_level(score)
        badges = compute_badge_tags(
            avg_rating=avg_rating,
            total_completed=total_completed,
            completion_rate=completion_rate,
            avg_response_minutes=avg_response_minutes,
            credit_level=level_key,
        )

        # Persist
        user.credit_score = score
        user.credit_level = level_key
        user.total_completed = total_completed
        user.total_disputed = total_disputed
        user.completion_rate = completion_rate
        user.avg_rating = avg_rating
        user.avg_response_minutes = avg_response_minutes
        user.badge_tags = badges

        await self._db.flush()
        await self._db.refresh(user)

        logger.info(
            "Recalculated credit for booster %s: score=%s level=%s",
            booster_id, score, level_key,
        )

        return user

    async def recalculate_all(self) -> int:
        """Recalculate credit for all boosters. Returns count."""
        result = await self._db.execute(
            select(User.id).where(User.role == UserRole.BOOSTER)
        )
        booster_ids = [row[0] for row in result]

        for bid in booster_ids:
            await self.recalculate(bid)

        return len(booster_ids)


def get_credit_service(db: AsyncSession) -> CreditService:
    return CreditService(db)
