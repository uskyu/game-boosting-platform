"""管理员：数据库容灾备份设置、手动触发、历史与下载。

路由前缀 ``/admin/backup``，全部要求 ``get_current_admin``。

真正的 mysqldump / 上传由 ``db_backup_service`` 的调度器执行；本模块只
读写配置、登记「手动请求时间」并展示运行状态，绝不在请求线程里跑备份，
也不直接操作 COS 对象。下载成功前会记一条审计日志（只含管理员 id、
记录 id、object_key 与字节数），便于事后追查敏感数据导出。
"""

import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select

from app.api.deps import DatabaseSession, get_current_admin
from app.models.database_backup import STATUS_RUNNING, STATUS_SUCCESS, DatabaseBackup
from app.models.site_setting import SiteSetting
from app.models.user import User
from app.schemas.backup import (
    BackupRunListResponse,
    BackupRunOut,
    BackupRunRequestResponse,
    BackupSettingsResponse,
    BackupSettingsUpdate,
)
from app.services import db_backup_service, storage_service
from app.services.site_setting_service import get_or_create_site_setting

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/backup", tags=["admin-backup"])

# 备份只能落 COS：即使当前上传模式是 LOCAL，也不会退回本地磁盘。
_COS_SETUP_HINT = "请先在对象存储中配置可用的腾讯云 COS（桶、地域、密钥）"


def _utc_now_naive() -> datetime:
    """与库内其他 DateTime 列一致：naive UTC。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_storage_mode(value: str | None) -> str:
    return value if value in ("LOCAL", "COS") else "LOCAL"


async def _latest_run(db) -> DatabaseBackup | None:
    return (
        await db.execute(
            select(DatabaseBackup)
            .order_by(DatabaseBackup.started_at.desc(), DatabaseBackup.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _is_running(
    setting: SiteSetting, last_run: DatabaseBackup | None
) -> bool:
    """正在跑、手动请求排队中或最近一条记录 RUNNING，都算「执行中」。

    服务启动时会清理遗留 RUNNING，因此正常情况下只有真正在跑的备份才会
    让前端继续 5 秒轮询；手动请求被调度器消费前也必须显示 running=true。
    """
    return (
        db_backup_service.is_running()
        or setting.db_backup_requested_at is not None
        or (last_run is not None and last_run.status == STATUS_RUNNING)
    )


async def _settings_response(
    db, setting: SiteSetting
) -> BackupSettingsResponse:
    """拼出 GET/PUT 共用的设置响应（含最近一次备份与下次运行时间）。"""
    last_run = await _latest_run(db)
    cos_config = await storage_service.get_cos_config(db)
    return BackupSettingsResponse(
        enabled=bool(setting.db_backup_enabled),
        interval_minutes=int(setting.db_backup_interval_minutes),
        retention_count=int(setting.db_backup_retention_count),
        prefix=setting.db_backup_prefix,
        include_notifications=bool(setting.db_backup_include_notifications),
        cos_configured=cos_config is not None,
        storage_mode=_normalize_storage_mode(setting.storage_mode),
        running=_is_running(setting, last_run),
        last_backup=(
            BackupRunOut.model_validate(last_run) if last_run is not None else None
        ),
        # 手动请求排队时这里返回 requested_at（序列化为 UTC Z）。
        next_run_at=db_backup_service.compute_next_run_at(setting, last_run),
    )


# =============================================================================
# 设置
# =============================================================================


@router.get(
    "/settings",
    response_model=BackupSettingsResponse,
    summary="读取数据库备份设置与运行状态",
)
async def get_backup_settings(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> BackupSettingsResponse:
    setting = await get_or_create_site_setting(db)
    return await _settings_response(db, setting)


@router.put(
    "/settings",
    response_model=BackupSettingsResponse,
    summary="保存数据库备份设置",
)
async def update_backup_settings(
    payload: BackupSettingsUpdate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> BackupSettingsResponse:
    setting = await get_or_create_site_setting(db)

    try:
        prefix = db_backup_service.normalize_backup_prefix(payload.prefix)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    # 开启自动备份前必须确认 COS 可用，避免调度器每轮都失败并刷失败记录。
    if payload.enabled and await storage_service.get_cos_config(db) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{_COS_SETUP_HINT}后再启用备份",
        )

    setting.db_backup_enabled = payload.enabled
    setting.db_backup_interval_minutes = payload.interval_minutes
    setting.db_backup_retention_count = payload.retention_count
    setting.db_backup_prefix = prefix
    setting.db_backup_include_notifications = payload.include_notifications
    await db.flush()
    await db.refresh(setting)
    return await _settings_response(db, setting)


# =============================================================================
# 手动触发
# =============================================================================


@router.post(
    "/run",
    response_model=BackupRunRequestResponse,
    summary="请求立即执行一次备份（调度器随后消费）",
)
async def request_backup_run(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> BackupRunRequestResponse:
    setting = await get_or_create_site_setting(db)
    last_run = await _latest_run(db)
    # 409 必须先于 COS 检查：并发请求时先告诉管理员「正在跑」，消息更准确。
    if _is_running(setting, last_run):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="已有备份正在执行，请稍后再试",
        )
    if await storage_service.get_cos_config(db) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{_COS_SETUP_HINT}后再执行备份",
        )

    # 即使自动开关关闭也允许手动备份：调度器看到非空请求会强制跑一次。
    setting.db_backup_requested_at = _utc_now_naive()
    await db.commit()
    return BackupRunRequestResponse(requested=True, running=True)


# =============================================================================
# 历史与下载
# =============================================================================


@router.get(
    "/runs",
    response_model=BackupRunListResponse,
    summary="备份历史（按开始时间倒序分页）",
)
async def list_backup_runs(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> BackupRunListResponse:
    total = int(
        (
            await db.execute(select(func.count()).select_from(DatabaseBackup))
        ).scalar()
        or 0
    )
    records = (
        (
            await db.execute(
                select(DatabaseBackup)
                .order_by(DatabaseBackup.started_at.desc(), DatabaseBackup.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return BackupRunListResponse(
        items=[BackupRunOut.model_validate(record) for record in records],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/runs/{run_id}/download",
    summary="下载一份成功的 gzip 备份",
)
async def download_backup_run(
    run_id: int,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> Response:
    record = await db.get(DatabaseBackup, run_id)
    # 只能下载已成功且确实有对象的记录；失败记录没有可用的备份文件。
    if (
        record is None
        or record.status != STATUS_SUCCESS
        or not record.object_key
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="备份记录不存在或没有可下载的备份文件",
        )

    cos_config = await storage_service.get_cos_config(db)
    if cos_config is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{_COS_SETUP_HINT}后再下载备份",
        )

    payload = await storage_service.read_bytes_for_config(
        record.object_key, cos_config, timeout_seconds=300
    )
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="备份对象不存在或已被清理",
        )
    data, _content_type = payload

    # 敏感数据导出审计：只记追踪标识，不记备份内容或任何密钥。
    logger.info(
        "Database backup downloaded admin_id=%s run_id=%s object_key=%s size_bytes=%s",
        current_admin.id,
        record.id,
        record.object_key,
        len(data),
    )

    # 文件名只取服务端写入的 object_key 的 basename，绝不用请求参数拼头。
    basename = record.object_key.rsplit("/", 1)[-1]
    return Response(
        content=data,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="{basename}"',
            # 备份是敏感数据：禁止浏览器/中间代理缓存这份响应。
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
