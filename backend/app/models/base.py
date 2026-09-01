"""
SQLAlchemy declarative base module.
Provides the base class for all ORM models with common utilities.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func

# Naming convention for database constraints
# This ensures consistent naming across all migrations
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """
    Base class for all SQLAlchemy ORM models.
    Provides common configuration and utilities.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert model instance to dictionary.
        Useful for serialization and debugging.
        """
        return {
            column.name: getattr(self, column.name)
            for column in self.__table__.columns
        }

    def __repr__(self) -> str:
        """
        Generate a readable string representation of the model.
        """
        class_name = self.__class__.__name__
        attrs = ", ".join(
            f"{column.name}={getattr(self, column.name)!r}"
            for column in self.__table__.columns
            if column.primary_key
        )
        return f"<{class_name}({attrs})>"


class TimestampMixin:
    """
    Mixin class that adds created_at and updated_at columns.
    Use with multiple inheritance: class MyModel(TimestampMixin, Base)
    """

    created_at: Mapped[datetime] = mapped_column(
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
