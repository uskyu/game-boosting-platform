"""
Notification service module.
Handles creation, querying, and read-state management for notifications.
Also manages user preference records.
"""

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationType, UserPreference


class NotificationService:
    """Business logic for notifications and user preferences."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Notification CRUD
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        user_id: int,
        type: NotificationType,
        title: str,
        content: str,
        link: str | None = None,
        ref_id: int | None = None,
    ) -> Notification:
        """Create a new notification and return it."""
        notification = Notification(
            user_id=user_id,
            type=type,
            title=title,
            content=content,
            link=link,
            ref_id=ref_id,
        )
        self.db.add(notification)
        await self.db.flush()
        await self.db.refresh(notification)
        return notification

    async def list_for_user(
        self,
        user_id: int,
        *,
        page: int = 1,
        page_size: int = 20,
        unread_only: bool = False,
    ) -> tuple[list[Notification], int, int]:
        """Return (items, total, unread_count) for the user."""
        base = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            base = base.where(Notification.is_read.is_(False))

        # total
        total_result = await self.db.execute(
            select(func.count()).select_from(base.subquery())
        )
        total = total_result.scalar_one()

        # unread count (always full, not filtered)
        unread_result = await self.db.execute(
            select(func.count()).where(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
        )
        unread_count = unread_result.scalar_one()

        # paginated items
        query = (
            base.order_by(Notification.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.db.execute(query)
        items = list(result.scalars().all())

        return items, total, unread_count

    async def get_unread_count(self, user_id: int) -> int:
        """Quick unread count for badge display."""
        result = await self.db.execute(
            select(func.count()).where(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
        )
        return result.scalar_one()

    async def mark_read(self, notification_id: int, user_id: int) -> Notification | None:
        """Mark a single notification as read."""
        result = await self.db.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
            )
        )
        notification = result.scalar_one_or_none()
        if notification and not notification.is_read:
            notification.is_read = True
            notification.read_at = datetime.utcnow()
            await self.db.flush()
        return notification

    async def mark_all_read(self, user_id: int) -> int:
        """Mark all unread notifications as read. Returns count updated."""
        now = datetime.utcnow()
        result = await self.db.execute(
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True, read_at=now)
        )
        await self.db.flush()
        return result.rowcount

    # ------------------------------------------------------------------
    # User preferences
    # ------------------------------------------------------------------

    async def get_preferences(self, user_id: int) -> UserPreference:
        """Get or create user preferences (lazy init)."""
        result = await self.db.execute(
            select(UserPreference).where(UserPreference.user_id == user_id)
        )
        pref = result.scalar_one_or_none()
        if pref is None:
            pref = UserPreference(user_id=user_id)
            self.db.add(pref)
            await self.db.flush()
            await self.db.refresh(pref)
        return pref

    async def update_preferences(
        self,
        user_id: int,
        *,
        notification_settings: dict | None = None,
        profile_visible: bool | None = None,
        show_online_status: bool | None = None,
        language: str | None = None,
    ) -> UserPreference:
        """Partial update of user preferences."""
        pref = await self.get_preferences(user_id)
        if notification_settings is not None:
            pref.notification_settings = notification_settings
        if profile_visible is not None:
            pref.profile_visible = profile_visible
        if show_online_status is not None:
            pref.show_online_status = show_online_status
        if language is not None:
            pref.language = language
        await self.db.flush()
        await self.db.refresh(pref)
        return pref

    async def should_notify(self, user_id: int, notification_type: NotificationType) -> bool:
        """Check if the user has this notification type enabled."""
        pref = await self.get_preferences(user_id)
        if pref.notification_settings is None:
            return True  # default: all on
        return pref.notification_settings.get(notification_type.value, True)


def get_notification_service(db: AsyncSession) -> NotificationService:
    """Factory for NotificationService."""
    return NotificationService(db)
