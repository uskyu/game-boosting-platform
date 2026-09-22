"""Authenticated user-facing announcement endpoints."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import or_, select

from app.api.deps import CurrentUser, DatabaseSession
from app.models.announcement import Announcement, AnnouncementFrequency, AnnouncementView
from app.schemas.announcement import AnnouncementPublicResponse

router = APIRouter(prefix="/announcements", tags=["announcements"])
# The deployment uses Asia/Shanghai and the backend image does not ship the
# optional system tzdata package. A fixed +08:00 offset is correct for this
# product timezone (which has no DST) and keeps the midnight rule deterministic.
PRODUCT_TIMEZONE = timezone(timedelta(hours=8))


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _local_date(value: datetime) -> object:
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return aware.astimezone(PRODUCT_TIMEZONE).date()


async def _find_eligible_announcement(db, user_id: int) -> Announcement | None:
    now = _utc_now_naive()
    result = await db.execute(
        select(Announcement)
        .where(
            Announcement.is_enabled.is_(True),
            or_(Announcement.start_at.is_(None), Announcement.start_at <= now),
            or_(Announcement.end_at.is_(None), Announcement.end_at > now),
        )
        .order_by(Announcement.priority.desc(), Announcement.updated_at.desc(), Announcement.id.desc())
    )
    announcements = result.scalars().all()
    if not announcements:
        return None

    announcement_ids = [item.id for item in announcements]
    view_result = await db.execute(
        select(AnnouncementView).where(
            AnnouncementView.user_id == user_id,
            AnnouncementView.announcement_id.in_(announcement_ids),
        )
    )
    views = {item.announcement_id: item for item in view_result.scalars().all()}
    today = _local_date(datetime.now(timezone.utc))

    for announcement in announcements:
        view = views.get(announcement.id)
        if announcement.frequency == AnnouncementFrequency.EVERY_OPEN:
            return announcement
        if view is None:
            return announcement
        if announcement.frequency == AnnouncementFrequency.DAILY and _local_date(view.last_shown_at) != today:
            return announcement
    return None


@router.get("/active", response_model=AnnouncementPublicResponse | None, summary="获取当前公告")
async def get_active_announcement(
    current_user: CurrentUser,
    db: DatabaseSession,
) -> AnnouncementPublicResponse | None:
    announcement = await _find_eligible_announcement(db, current_user.id)
    if announcement is None:
        return None
    return AnnouncementPublicResponse(
        id=announcement.id,
        title=announcement.title,
        content_html=announcement.safe_content_html,
        frequency=announcement.frequency,
    )


@router.post("/{announcement_id}/shown", summary="记录公告已展示")
async def mark_announcement_shown(
    announcement_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> dict:
    announcement = await db.get(Announcement, announcement_id)
    if announcement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="公告不存在")

    result = await db.execute(
        select(AnnouncementView)
        .where(
            AnnouncementView.announcement_id == announcement_id,
            AnnouncementView.user_id == current_user.id,
        )
        .with_for_update()
    )
    view = result.scalar_one_or_none()
    now = _utc_now_naive()
    if view is None:
        view = AnnouncementView(
            announcement_id=announcement_id,
            user_id=current_user.id,
            last_shown_at=now,
        )
        db.add(view)
    else:
        view.last_shown_at = now
    await db.flush()
    return {"success": True}
