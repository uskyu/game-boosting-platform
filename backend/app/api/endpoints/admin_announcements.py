"""Administrator CRUD endpoints for platform-wide announcements."""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select

from app.api.deps import DatabaseSession, get_current_admin
from app.models.announcement import Announcement
from app.models.user import User
from app.schemas.announcement import (
    AnnouncementAdminResponse,
    AnnouncementCreate,
    AnnouncementPreviewRequest,
    AnnouncementPreviewResponse,
    AnnouncementUpdate,
)
from app.services.html_sanitizer import sanitize_announcement_html

router = APIRouter(prefix="/admin/announcements", tags=["admin-announcements"])


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _safe_content(value: str) -> str:
    safe = sanitize_announcement_html(value)
    if not safe:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="公告内容清洗后为空，请填写可展示的文字或 HTML",
        )
    return safe


def _response(announcement: Announcement) -> AnnouncementAdminResponse:
    return AnnouncementAdminResponse.model_validate(announcement)


@router.get("", response_model=list[AnnouncementAdminResponse], summary="公告列表")
async def list_announcements(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> list[AnnouncementAdminResponse]:
    result = await db.execute(
        select(Announcement).order_by(desc(Announcement.priority), desc(Announcement.updated_at), desc(Announcement.id))
    )
    return [_response(item) for item in result.scalars().all()]


@router.post("/preview", response_model=AnnouncementPreviewResponse, summary="预览公告 HTML")
async def preview_announcement(
    payload: AnnouncementPreviewRequest,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> AnnouncementPreviewResponse:
    return AnnouncementPreviewResponse(safe_content_html=_safe_content(payload.content_html))


@router.post("", response_model=AnnouncementAdminResponse, status_code=status.HTTP_201_CREATED, summary="创建公告")
async def create_announcement(
    payload: AnnouncementCreate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> AnnouncementAdminResponse:
    announcement = Announcement(
        title=payload.title,
        content_html=payload.content_html,
        safe_content_html=_safe_content(payload.content_html),
        frequency=payload.frequency,
        is_enabled=payload.is_enabled,
        priority=payload.priority,
        start_at=_utc_naive(payload.start_at),
        end_at=_utc_naive(payload.end_at),
        created_by=current_admin.id,
    )
    db.add(announcement)
    await db.flush()
    await db.refresh(announcement)
    return _response(announcement)


@router.put("/{announcement_id}", response_model=AnnouncementAdminResponse, summary="修改公告")
async def update_announcement(
    announcement_id: int,
    payload: AnnouncementUpdate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> AnnouncementAdminResponse:
    announcement = await db.get(Announcement, announcement_id)
    if announcement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="公告不存在")

    announcement.title = payload.title
    announcement.content_html = payload.content_html
    announcement.safe_content_html = _safe_content(payload.content_html)
    announcement.frequency = payload.frequency
    announcement.is_enabled = payload.is_enabled
    announcement.priority = payload.priority
    announcement.start_at = _utc_naive(payload.start_at)
    announcement.end_at = _utc_naive(payload.end_at)
    await db.flush()
    await db.refresh(announcement)
    return _response(announcement)


@router.delete("/{announcement_id}", summary="删除公告")
async def delete_announcement(
    announcement_id: int,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> dict:
    announcement = await db.get(Announcement, announcement_id)
    if announcement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="公告不存在")
    await db.delete(announcement)
    return {"success": True}
