"""Public site settings endpoints."""

from fastapi import APIRouter

from app.api.deps import DatabaseSession
from app.models.site_setting import SiteSetting
from app.schemas.site_setting import SiteSettingResponse
from app.services.site_setting_service import get_or_create_site_setting

router = APIRouter(prefix="/site", tags=["site"])

__all__ = ["router", "get_or_create_site_setting"]


@router.get("/settings", response_model=SiteSettingResponse, summary="获取站点设置")
async def get_site_settings(db: DatabaseSession) -> SiteSetting:
    """Return public branding settings, creating the default row on first access."""
    return await get_or_create_site_setting(db)
