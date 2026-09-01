"""
User model module.
Defines the User entity with authentication and role management.
"""

from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.order import Order


class UserRole(str, PyEnum):
    """
    Enumeration of user roles in the platform.
    Inherits from str for JSON serialization compatibility.
    """

    USER = "USER"           # Regular customer who places orders
    BOOSTER = "BOOSTER"     # Service provider who fulfills orders
    ADMIN = "ADMIN"         # Platform administrator


class BoosterApplicationStatus(str, PyEnum):
    """Status for a normal user's booster application."""

    NONE = "NONE"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class User(Base):
    """
    User model representing all platform users.

    Supports three roles:
    - USER: Customers who create boosting orders
    - BOOSTER: Professionals who complete boosting orders
    - ADMIN: Platform administrators with full access
    """

    __tablename__ = "users"

    # Primary key
    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        index=True,
    )

    # Authentication fields
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )

    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # Profile fields. username is unique so it cannot be used to
    # impersonate another account (system messages render by username).
    username: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
        nullable=False,
    )

    # Role management
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=UserRole.USER,
        nullable=False,
        index=True,
    )

    # Account status
    is_active: Mapped[bool] = mapped_column(
        default=True,
        nullable=False,
    )

    is_verified: Mapped[bool] = mapped_column(
        default=False,
        nullable=False,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    # Optional profile fields
    avatar_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    phone: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    bio: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Booster application workflow
    booster_application_status: Mapped[BoosterApplicationStatus] = mapped_column(
        Enum(
            BoosterApplicationStatus,
            name="booster_application_status_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=BoosterApplicationStatus.NONE,
        nullable=False,
        index=True,
    )

    booster_application_game: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    booster_application_current_rank: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    booster_application_target_rank: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    booster_application_proof_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    booster_application_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    booster_quota: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
    )

    reviewed_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
    )

    review_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Reputation / credit system (boosters only)
    credit_score: Mapped[int] = mapped_column(
        default=100,
        nullable=False,
    )

    credit_level: Mapped[str] = mapped_column(
        String(20),
        default="bronze",
        nullable=False,
    )

    total_completed: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
    )

    total_disputed: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
    )

    completion_rate: Mapped[float] = mapped_column(
        Numeric(precision=5, scale=2),
        default=0.0,
        nullable=False,
    )

    avg_rating: Mapped[float] = mapped_column(
        Numeric(precision=3, scale=2),
        default=0.0,
        nullable=False,
    )

    avg_response_minutes: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
    )

    badge_tags: Mapped[list[str] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # Relationships
    # Orders created by this user (as customer)
    orders_as_customer: Mapped[list["Order"]] = relationship(
        "Order",
        back_populates="user",
        foreign_keys="Order.user_id",
        lazy="selectin",
        cascade="save-update, merge",
    )

    # Orders assigned to this user (as booster)
    orders_as_booster: Mapped[list["Order"]] = relationship(
        "Order",
        back_populates="booster",
        foreign_keys="Order.booster_id",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email={self.email!r}, role={self.role.value})>"
