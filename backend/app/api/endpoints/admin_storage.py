"""管理员：对象存储（腾讯云 COS）配置、迁移与本地清理。

路由前缀 ``/admin/storage``，全部要求 ``get_current_admin``。

安全口径：
* SecretId / SecretKey 落库前经 ``encrypt_text`` 加密，任何响应只回**掩码**；
* ``cos_secret_id`` / ``cos_secret_key`` 提交 ``None``、空串、``__KEEP__``
  或回显的掩码都表示「保持已存密文」，便于前端保存其他字段时不重输密钥。

任务状态（迁移/清理）保存在本模块的**进程内单例** ``_StorageJob``。应用按
单 uvicorn worker 运行（docker-entrypoint.sh 未传 ``--workers``），因此不需要
跨进程协调；若将来改成多 worker，必须把任务句柄与互斥改到数据库或外部队列，
否则每个 worker 会各起一份任务。
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import DatabaseSession, get_current_admin
from app.core.security import decrypt_text, encrypt_text
from app.db.session import async_session_factory
from app.models.site_setting import SiteSetting
from app.models.storage_migration import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PURGED as STORAGE_STATUS_PURGED,
    StorageMigration,
)
from app.models.user import User
from app.schemas.storage import (
    MigrationStatusResponse,
    StorageCleanupStatusResponse,
    StorageSettingsResponse,
    StorageSettingsUpdate,
    StorageTestRequest,
    StorageTestResponse,
    is_keep_sentinel,
    mask_secret_id,
    mask_secret_key,
)
from app.services import storage_migration, storage_service
from app.services.site_setting_service import get_or_create_site_setting

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/storage", tags=["admin-storage"])


@dataclass
class _StorageJob:
    """进程内后台任务句柄（单 worker 部署；不假设可跨进程共享）。"""

    task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()


# 迁移与清理各一个任务句柄；两者前置条件互斥，正常不会并行。
migration_job = _StorageJob()
cleanup_job = _StorageJob()


def _log_task_result(task: asyncio.Task, label: str) -> None:
    """后台任务外层异常只记日志：请求方靠状态接口感知进度/失败。

    逐文件错误已由 worker 记录在台账里；这里兜住「数据库不可用」这类
    整轮失败，避免 ``Task exception was never retrieved`` 噪声。
    """
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001 - 后台任务不能把异常抛回请求
        logger.exception("对象存储%s任务异常退出", label)


def _spawn(job: _StorageJob, coro, label: str) -> None:
    task = asyncio.create_task(coro)
    job.task = task
    task.add_done_callback(lambda finished: _log_task_result(finished, label))


async def _load_site_setting(db) -> SiteSetting:
    """读取单行站点设置；首次部署缺行时按 env 默认值懒创建。"""
    return await get_or_create_site_setting(db)


def _normalize_mode(value: str | None) -> str:
    return value if value in ("LOCAL", "COS") else "LOCAL"


def _saved_secrets(setting: SiteSetting) -> tuple[str | None, str | None]:
    """解密已保存的凭据；缺列/坏密文一律 None（由调用方判定是否可测）。"""
    return decrypt_text(setting.cos_secret_id), decrypt_text(setting.cos_secret_key)


def _settings_response(setting: SiteSetting) -> StorageSettingsResponse:
    """后台视图：密钥只回掩码，永不回明文。"""
    secret_id, secret_key = _saved_secrets(setting)
    return StorageSettingsResponse(
        storage_mode=_normalize_mode(setting.storage_mode),
        cos_bucket=setting.cos_bucket,
        cos_region=setting.cos_region,
        cos_cdn_domain=setting.cos_cdn_domain,
        cos_secret_id_masked=mask_secret_id(secret_id),
        cos_secret_key_masked=mask_secret_key(secret_key),
        # 四项齐备才认为可用于 COS 读写；缺任一项前端应提示补全
        cos_configured=bool(
            secret_id and secret_key and setting.cos_bucket and setting.cos_region
        ),
    )


def _effective_credential(
    incoming: str | None, saved_cipher: str | None
) -> tuple[str | None, str | None]:
    """返回 (生效明文, 应落库的密文)。

    ``incoming`` 为 None/空串/``__KEEP__``/掩码时沿用已存密文并解密出
    明文用于连接校验；否则把新明文加密后落库。
    """
    if is_keep_sentinel(incoming):
        return decrypt_text(saved_cipher), saved_cipher
    plain = (incoming or "").strip() or None
    return plain, encrypt_text(plain)


async def _probe(cfg) -> tuple[bool, str]:
    """调用连通性测试；``test_cos_connection`` 约定不抛异常，这里再兜一层。"""
    try:
        ok, message = await storage_service.test_cos_connection(cfg)
    except Exception as exc:  # noqa: BLE001 - 测试接口不应 500
        return False, f"连接测试失败：{str(exc)[:300]}"
    return bool(ok), (message or ("连接成功" if ok else "连接失败"))


def _candidate_config(
    setting: SiteSetting, payload: StorageSettingsUpdate | None = None
) -> storage_service.StorageConfig:
    """拼出用于连通性测试的候选配置。

    密钥字段走「保持原值」判定；非密钥字段仅在请求显式携带时覆盖，未传的
    字段沿用已保存值。这样「只改桶名」「只换密钥」都能正确被测到。
    """
    if payload is None:
        secret_id, _ = _saved_secrets(setting)
        secret_key, _ = _saved_secrets(setting)
        bucket, region, cdn = (
            setting.cos_bucket,
            setting.cos_region,
            setting.cos_cdn_domain,
        )
    else:
        secret_id, _ = _effective_credential(payload.cos_secret_id, setting.cos_secret_id)
        secret_key, _ = _effective_credential(
            payload.cos_secret_key, setting.cos_secret_key
        )
        fields_set = payload.model_fields_set
        bucket = (
            payload.cos_bucket if "cos_bucket" in fields_set else setting.cos_bucket
        )
        region = (
            payload.cos_region if "cos_region" in fields_set else setting.cos_region
        )
        cdn = (
            payload.cos_cdn_domain
            if "cos_cdn_domain" in fields_set
            else setting.cos_cdn_domain
        )
    return storage_service.StorageConfig(
        mode="COS",
        secret_id=secret_id,
        secret_key=secret_key,
        bucket=bucket,
        region=region,
        cdn_domain=cdn,
    )


# =============================================================================
# 设置
# =============================================================================

@router.get(
    "/settings",
    response_model=StorageSettingsResponse,
    summary="读取对象存储设置",
)
async def get_storage_settings(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> StorageSettingsResponse:
    return _settings_response(await _load_site_setting(db))


@router.put(
    "/settings",
    response_model=StorageSettingsResponse,
    summary="保存对象存储设置",
)
async def update_storage_settings(
    payload: StorageSettingsUpdate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> StorageSettingsResponse:
    setting = await _load_site_setting(db)

    # 密钥：None/空串/__KEEP__/掩码 = 保持已存密文（前端保存其他字段不必重输）
    secret_id_plain, secret_id_cipher = _effective_credential(
        payload.cos_secret_id, setting.cos_secret_id
    )
    secret_key_plain, secret_key_cipher = _effective_credential(
        payload.cos_secret_key, setting.cos_secret_key
    )

    fields_set = payload.model_fields_set
    bucket = payload.cos_bucket if "cos_bucket" in fields_set else setting.cos_bucket
    region = payload.cos_region if "cos_region" in fields_set else setting.cos_region
    cdn_domain = (
        payload.cos_cdn_domain
        if "cos_cdn_domain" in fields_set
        else setting.cos_cdn_domain
    )

    # 切到 COS 前必须先验证：写路径一旦切过去，上传失败就是线上事故。
    # 校验失败时直接 400，且**本次保存整体不生效**（不提交任何字段）。
    # 其他字段仍可照常保存：保持 storage_mode=LOCAL 提交即可（此时不做网络
    # 探测），或在修正凭据后重试；被拒绝的只是「把线上存储切到一个连不上
    # 的 COS」这一个动作，而不是整个设置页不可用。
    if payload.storage_mode == "COS":
        candidate = storage_service.StorageConfig(
            mode="COS",
            secret_id=secret_id_plain,
            secret_key=secret_key_plain,
            bucket=bucket,
            region=region,
            cdn_domain=cdn_domain,
        )
        ok, message = await _probe(candidate)
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"无法切换到 COS：{message}",
            )

    setting.storage_mode = payload.storage_mode
    setting.cos_secret_id = secret_id_cipher
    setting.cos_secret_key = secret_key_cipher
    setting.cos_bucket = bucket
    setting.cos_region = region
    setting.cos_cdn_domain = cdn_domain
    setting.updated_by = current_admin.id
    await db.flush()
    await db.refresh(setting)
    # 进程内配置缓存立即失效：否则上传/读取还会按旧模式跑最多 2 秒。
    storage_service.invalidate_storage_config_cache()
    return _settings_response(setting)


@router.post(
    "/test",
    response_model=StorageTestResponse,
    summary="测试 COS 连接（可测试未保存的凭据）",
)
async def test_storage_connection(
    payload: StorageTestRequest,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> StorageTestResponse:
    setting = await _load_site_setting(db)
    # 任一字段缺省/空串就回落到已保存（解密后）的配置，前端可「先测后存」。
    # 这里刻意不经过 storage_service.get_storage_config：当前模式为 LOCAL 时
    # 它返回的是无凭据的本地配置，会丢掉已保存的 COS 凭据。
    candidate = _candidate_config(setting, payload)
    if not (
        candidate.secret_id
        and candidate.secret_key
        and candidate.bucket
        and candidate.region
    ):
        return StorageTestResponse(
            ok=False,
            message="请先填写完整的 SecretId、SecretKey、Bucket 与 Region（或先保存配置）",
        )

    ok, message = await _probe(candidate)
    return StorageTestResponse(ok=ok, message=message)


# =============================================================================
# 迁移
# =============================================================================

async def _migration_status(db) -> MigrationStatusResponse:
    """台账实时聚合 + 进程内任务状态 + 磁盘实况。"""
    counts = {
        str(status_value): int(count)
        for status_value, count in (
            await db.execute(
                select(StorageMigration.status, func.count()).group_by(
                    StorageMigration.status
                )
            )
        ).all()
    }
    total = sum(counts.values())
    # PURGED 也是「已完成」：对象已在 COS、本地副本已按管理员指令删除。
    # 只数 DONE 会让清理完成后的进度条倒退回 0/pending=N，老板会以为迁移失败
    # （2026-11-23 实测：清理 20 个后 status 显示 done=0 pending=20）。
    done = counts.get(STATUS_DONE, 0) + counts.get(STORAGE_STATUS_PURGED, 0)
    failed = counts.get(STATUS_FAILED, 0)
    pending = total - done - failed
    local_files, local_bytes = storage_migration.local_files_summary()
    progress = storage_migration.migration_progress
    return MigrationStatusResponse(
        total=total,
        done=done,
        failed=failed,
        pending=max(0, pending),
        running=migration_job.running or progress.running,
        paused=progress.paused,
        current_path=progress.current_path,
        last_error=progress.last_error,
        local_files=local_files,
        local_bytes=local_bytes,
    )


async def _require_effective_cos(db) -> storage_service.StorageConfig:
    """确认「当前生效」的存储配置确实是 COS（含凭据齐备）。

    ``site_settings.storage_mode`` 可能写着 COS 但凭据不全，此时
    ``get_storage_config`` 会退回 LOCAL；迁移/清理若继续跑，会把本地文件
    「迁移」成同一份本地副本再当成已迁移，清理阶段就会删掉唯一副本。
    """
    cfg = await storage_service.get_storage_config(db)
    if cfg.mode != "COS":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "当前生效的存储方式不是 COS（可能尚未切换或 COS 配置不完整），"
                "请先保存完整的 COS 配置并测试连接成功"
            ),
        )
    return cfg


@router.post(
    "/migrate",
    response_model=MigrationStatusResponse,
    summary="开始/继续本地文件迁移到 COS",
)
async def start_migration(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> MigrationStatusResponse:
    setting = await _load_site_setting(db)
    if _normalize_mode(setting.storage_mode) != "COS":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="当前存储方式为本地磁盘，请先切换到 COS 并保存成功后再迁移历史文件",
        )
    await _require_effective_cos(db)

    # 已在跑：只回状态，不重复启动（重复入账虽幂等，但并发跑两份没意义）。
    if migration_job.running:
        return await _migration_status(db)

    storage_migration.clear_pause()
    _spawn(
        migration_job,
        storage_migration.run_migration(async_session_factory),
        "迁移",
    )
    return await _migration_status(db)


@router.post(
    "/migrate/pause",
    response_model=MigrationStatusResponse,
    summary="暂停迁移（当前文件处理完后停止）",
)
async def pause_migration(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> MigrationStatusResponse:
    if migration_job.running:
        storage_migration.request_pause()
    return await _migration_status(db)


@router.get(
    "/migrate/status",
    response_model=MigrationStatusResponse,
    summary="查询迁移进度",
)
async def migration_status(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> MigrationStatusResponse:
    return await _migration_status(db)


# =============================================================================
# 清理本地文件
# =============================================================================

def _cleanup_status() -> StorageCleanupStatusResponse:
    progress = storage_migration.cleanup_progress
    return StorageCleanupStatusResponse(
        running=cleanup_job.running or progress.running,
        deleted=progress.deleted,
        failed=progress.failed,
        remaining=progress.remaining,
        last_error=progress.last_error,
    )


@router.post(
    "/cleanup-local",
    response_model=StorageCleanupStatusResponse,
    summary="删除已迁移完成的本地文件",
)
async def start_cleanup(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> StorageCleanupStatusResponse:
    if cleanup_job.running:
        return _cleanup_status()

    # 前置条件一：台账里不能有任何非 DONE 行（PENDING/FAILED 意味着本地
    # 文件还是唯一副本），否则拒绝并告知数量。
    blocked = int(
        (
            await db.execute(
                select(func.count())
                .select_from(StorageMigration)
                .where(StorageMigration.status != STATUS_DONE)
            )
        ).scalar()
        or 0
    )
    if blocked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"还有 {blocked} 个文件未迁移完成（PENDING/FAILED），"
                "请先完成迁移（失败文件需修复后重试），再清理本地文件"
            ),
        )

    local_files, _ = storage_migration.local_files_summary()
    if local_files == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="本地没有需要清理的文件",
        )

    # 前置条件二：必须真的在用 COS。否则清理任务里的 exists() 校验的是本地
    # 磁盘（必然为真），会把唯一副本删掉。
    await _require_effective_cos(db)

    storage_migration.reset_cleanup_progress()
    _spawn(
        cleanup_job,
        storage_migration.run_cleanup(async_session_factory),
        "清理",
    )
    return _cleanup_status()


@router.get(
    "/cleanup/status",
    response_model=StorageCleanupStatusResponse,
    summary="查询本地清理进度",
)
async def cleanup_status(
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> StorageCleanupStatusResponse:
    return _cleanup_status()
