"""Review model module. Bidirectional reviews between users and boosters."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.order import Order
    from app.models.user import User


class Review(Base):
    """Review for a completed order. One review per reviewer per order."""

    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("order_id", "reviewer_id", name="uq_reviews_order_reviewer"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    reviewer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    target_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    rating: Mapped[int] = mapped_column(Integer, nullable=False)

    content: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Relationships
    order: Mapped["Order"] = relationship("Order", lazy="joined")
    reviewer: Mapped["User"] = relationship("User", foreign_keys=[reviewer_id], lazy="joined")
    target: Mapped["User"] = relationship("User", foreign_keys=[target_id], lazy="joined")

    def __repr__(self) -> str:
        return f"<Review(id={self.id}, order={self.order_id}, rating={self.rating})>"
