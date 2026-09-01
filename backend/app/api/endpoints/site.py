"""Public site settings endpoints."""

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import DatabaseSession
from app.models.site_setting import SiteSetting
from app.schemas.site_setting import SiteSettingResponse

router = APIRouter(prefix="/site", tags=["site"])


async def get_or_create_site_setting(db) -> SiteSetting:
    result = await db.execute(select(SiteSetting).where(SiteSetting.id == 1))
    setting = result.scalar_one_or_none()
    if setting is None:
        setting = SiteSetting(id=1, site_name="游戏服务平台")
        db.add(setting)
        await db.flush()
        await db.refresh(setting)
    return setting


@router.get("/settings", response_model=SiteSettingResponse, summary="获取站点设置")
async def get_site_settings(db: DatabaseSession) -> SiteSetting:
    """Return public branding settings, creating the default row on first access."""
    return await get_or_create_site_setting(db)
