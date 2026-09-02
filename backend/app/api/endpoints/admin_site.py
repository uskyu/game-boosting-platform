"""Administrator endpoints for site branding settings."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile, status

from app.api.deps import DatabaseSession, get_current_admin
from app.api.endpoints.site import get_or_create_site_setting
from app.core.config import settings
from app.models.site_setting import SiteSetting
from app.models.user import User
from app.schemas.site_setting import SiteSettingResponse, SiteSettingUpdate
from app.schemas.user import MessageResponse
from app.services.file_service import save_image_upload

router = APIRouter(prefix="/admin/site", tags=["admin-site"])


def _remove_site_file(url: str | None) -> None:
    if not url or not url.startswith("/uploads/site/"):
        return
    root = Path(settings.UPLOAD_DIR).resolve()
    candidate = (root / url.removeprefix("/uploads/")).resolve()
    if candidate.parent == root / "site" and candidate.is_file():
        candidate.unlink(missing_ok=True)


@router.put("/settings", response_model=SiteSettingResponse, summary="修改站点设置")
async def update_site_settings(
    payload: SiteSettingUpdate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> SiteSetting:
    setting = await get_or_create_site_setting(db)
    setting.site_name = payload.site_name
    setting.site_description = payload.site_description
    setting.updated_by = current_admin.id
    await db.flush()
    await db.refresh(setting)
    return setting


@router.put("/logo", response_model=SiteSettingResponse, summary="上传站点 Logo")
async def upload_site_logo(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    logo: UploadFile = File(...),
) -> SiteSetting:
    setting = await get_or_create_site_setting(db)
    old_url = setting.site_logo_url
    new_url = await save_image_upload(logo, "site", max_size_bytes=10 * 1024 * 1024)
    setting.site_logo_url = new_url
    setting.updated_by = current_admin.id
    await db.flush()
    _remove_site_file(old_url)
    await db.refresh(setting)
    return setting


@router.delete("/logo", response_model=MessageResponse, summary="删除站点 Logo")
async def delete_site_logo(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> MessageResponse:
    setting = await get_or_create_site_setting(db)
    old_url = setting.site_logo_url
    setting.site_logo_url = None
    setting.updated_by = current_admin.id
    await db.flush()
    _remove_site_file(old_url)
    return MessageResponse(message="站点 Logo 已恢复默认", success=True)
