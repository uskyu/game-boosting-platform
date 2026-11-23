"""本地文件 → 腾讯云 COS 的可中断迁移/清理任务。

设计要点（单 uvicorn worker 部署）：

* 台账 ``storage_migrations`` 是唯一事实来源：扫描到的每个文件一行，
  ``DONE`` 行即幂等标记，重复执行迁移只会重试 ``PENDING`` 与未超次数的
  ``FAILED``，因此任务被暂停/进程重启后可以直接续跑。
* 迁移与清理都**按批新建数据库会话**（``app_db_factory()``），绝不把请求
  会话带进后台任务：请求结束会关闭会话，复用它是典型的悬空连接来源。
* 迁移阶段只读本地文件、只写 COS 与台账，**从不删除本地文件**；真正的
  删除集中在 ``run_cleanup``，且只删「台账 DONE + COS 验证存在」的文件。
* 暂停只是「处理完当前文件后不再取下一批」，不打断正在上传的那个文件，
  避免留下半截状态。
"""

import asyncio
import logging
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import anyio
from sqlalchemy import or_, select

from app.core.config import settings
from app.models.storage_migration import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_PURGED,
    StorageMigration,
)
from app.services import storage_service

logger = logging.getLogger(__name__)

# 每批处理条数与单条之间的让出间隔：批内逐条 commit，批间重新建会话，
# 既能让事件循环处理请求，也把单次事务的持锁时间压到一条记录的量级。
BATCH_SIZE = 20
ITEM_SLEEP_SECONDS = 0.05
# FAILED 行的自动重试上限；超过后交给管理员人工判断（例如源文件损坏）。
MAX_ATTEMPTS = 3


def _utc_now() -> datetime:
    """库内统一存 naive UTC，与项目其他 DateTime 列保持一致。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _invalidate_signing_exists_cache() -> None:
    """让签名层的「对象是否在 COS」缓存失效。

    迁移/清理刚改变了 COS 里的对象集合，签名层若还缓存着旧结论（尤其是
    「不在 COS」），新迁移的图会继续被当作本地图处理，最多迟滞一个缓存 TTL。
    这里延迟 import：签名模块与迁移模块互不依赖，避免顶层循环引用。
    """
    try:
        from app.services import url_signing

        url_signing.invalidate_exists_cache()
    except Exception:  # noqa: BLE001 - 缓存失效失败不应影响迁移结果
        logger.warning("invalidate signing exists cache failed", exc_info=True)


def _local_path(rel_path: str) -> Path:
    return Path(settings.UPLOAD_DIR) / rel_path


def guess_content_type(rel_path: str) -> str:
    """按扩展名推断 Content-Type；未知类型回落到二进制流。"""
    guessed, _ = mimetypes.guess_type(rel_path)
    return guessed or "application/octet-stream"


def _short_error(exc: BaseException) -> str:
    """失败原因转中文可读文本并截断，避免把超长上游响应原样写进库。"""
    text = str(exc).strip() or exc.__class__.__name__
    return f"迁移失败：{text}"[:1000]


# =============================================================================
# 任务进度（进程内状态；单 worker 部署，不做跨进程协调）
# =============================================================================

@dataclass
class MigrationProgress:
    """迁移任务的易变状态；统计数字一律从台账实时聚合，不放这里。"""

    running: bool = False
    paused: bool = False
    current_path: str | None = None
    last_error: str | None = None


@dataclass
class CleanupProgress:
    """清理本地文件的计数（台账里没有这些数字，只能进程内维护）。"""

    running: bool = False
    deleted: int = 0
    failed: int = 0
    remaining: int = 0
    last_error: str | None = None


migration_progress = MigrationProgress()
cleanup_progress = CleanupProgress()

_pause_requested = False


def request_pause() -> None:
    """请求「当前文件处理完就停」，可重复调用。"""
    global _pause_requested
    _pause_requested = True
    migration_progress.paused = True


def clear_pause() -> None:
    """开始新一轮迁移前清除暂停标记。"""
    global _pause_requested
    _pause_requested = False
    migration_progress.paused = False


def pause_requested() -> bool:
    return _pause_requested


def reset_cleanup_progress() -> None:
    """启动新一轮清理前把计数归零（上一轮的 deleted/failed 不再累计）。"""
    cleanup_progress.deleted = 0
    cleanup_progress.failed = 0
    cleanup_progress.remaining = len(scan_local_files())
    cleanup_progress.last_error = None


# =============================================================================
# 扫描与入账
# =============================================================================

def scan_local_files() -> list[tuple[str, int]]:
    """遍历 ``UPLOAD_DIR``，返回 ``(rel_path, size)`` 并按路径字典序排序。

    排序保证同一目录在任意时刻扫描得到的顺序稳定，断点续跑时进度可预测。
    跳过符号链接：它们可能指向目录树外，既不该迁移也不该被清理。
    """
    root = Path(settings.UPLOAD_DIR)
    if not root.is_dir():
        return []

    files: list[tuple[str, int]] = []
    for path in root.rglob("*"):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            files.append((path.relative_to(root).as_posix(), path.stat().st_size))
        except OSError as exc:
            # 单个文件不可读不该让整轮扫描失败（权限/竞态删除都可能）
            logger.warning("扫描本地文件时跳过 %s：%s", path, exc)
    files.sort(key=lambda item: item[0])
    return files


def local_files_summary() -> tuple[int, int]:
    """磁盘上仍存在的文件数与总字节数（迁移/清理状态页用的实时值）。"""
    files = scan_local_files()
    return len(files), sum(size for _, size in files)


async def enqueue_ledger(session, files: list[tuple[str, int]]) -> int:
    """把扫描结果补进台账：缺失的 ``rel_path`` 以 PENDING 入账。

    已存在的行一律不动（DONE 保持 DONE、FAILED 保留失败原因与次数），
    这是「可重复点击开始迁移」的关键。唯一键是最终防线，即使并发扫描
    撞上重复插入也只回滚不产生脏数据。返回新插入的行数。
    """
    if not files:
        return 0

    inserted = 0
    # 分批 IN 查询，避免一次扫描上千文件时拼出超长 SQL。
    for start in range(0, len(files), 500):
        chunk = files[start : start + 500]
        existing = set(
            (
                await session.execute(
                    select(StorageMigration.rel_path).where(
                        StorageMigration.rel_path.in_([rel for rel, _ in chunk])
                    )
                )
            )
            .scalars()
            .all()
        )
        missing = [(rel, size) for rel, size in chunk if rel not in existing]
        if not missing:
            continue
        session.add_all(
            [
                StorageMigration(
                    rel_path=rel,
                    size_bytes=size,
                    status=STATUS_PENDING,
                    attempts=0,
                )
                for rel, size in missing
            ]
        )
        await session.flush()
        inserted += len(missing)

    return inserted


# =============================================================================
# 迁移
# =============================================================================

async def _read_local_bytes(rel_path: str) -> bytes:
    """在事件循环外读文件：10MB 级上传文件同步读会明显卡住其他请求。"""
    path = _local_path(rel_path)
    return await anyio.to_thread.run_sync(path.read_bytes)


async def _mark_failed(session, rel_path: str, message: str) -> None:
    """把单条台账标记为 FAILED 并累加尝试次数。

    按 ``rel_path`` 重新取行：上传失败时 ORM 行可能已因 rollback 过期，
    重新 select 比复用旧实例更可靠（唯一键保证只命中一行）。
    """
    row = (
        await session.execute(
            select(StorageMigration).where(StorageMigration.rel_path == rel_path)
        )
    ).scalar_one_or_none()
    if row is None:  # pragma: no cover - 台账行在扫描后被人为删除
        return
    row.status = STATUS_FAILED
    row.error = message
    row.attempts = int(row.attempts or 0) + 1
    await session.commit()


async def _mark_purged(session, rel_path: str) -> None:
    """把台账行标记为 PURGED（已迁移到 COS 且本地副本已删除）。

    清理阶段在删除本地文件后必须调用：否则该行仍是 DONE，下一轮迁移扫描会
    把它当"待迁移"再选中，然后因源文件不存在而报 FAILED（2026-11-23 实测）。
    行不存在（台账被人工清过）时静默跳过。
    """
    row = (
        await session.execute(
            select(StorageMigration).where(StorageMigration.rel_path == rel_path)
        )
    ).scalar_one_or_none()
    if row is None:  # pragma: no cover - 台账行在扫描后被人为删除
        return
    row.status = STATUS_PURGED
    row.error = None
    await session.commit()


async def _migrate_one(session, rel_path: str, cos_config) -> None:
    """迁移单个文件：读本地 → 上传 COS → COS 侧 exists 校验 → 标记 DONE。

    ``cos_config`` 由调用方显式传入（``get_storage_config`` 解析后的 COS
    配置），**不跟随当前 storage_mode**：管理员在迁移途中把模式切回 LOCAL
    时，旧实现会写本地/查本地，于是把 COS 里根本没有的对象误记为 DONE，
    清理阶段再去 COS 校验就全部 NoSuchResource（2026-11-23 实测踩到）。
    迁移的语义是「把本地文件复制到 COS」，与当前生效模式无关。
    """
    data = await _read_local_bytes(rel_path)
    await storage_service.save_bytes_for_config(
        data, rel_path, guess_content_type(rel_path), cos_config
    )
    if not await storage_service.exists_for_config(rel_path, cos_config):
        # 上传接口返回成功但对象不存在（常见于配置错桶/后缀路径错误），
        # 不能标记 DONE，否则后续清理会把本地唯一副本删掉。
        raise RuntimeError("上传后校验失败：COS 中找不到该对象")

    row = (
        await session.execute(
            select(StorageMigration).where(StorageMigration.rel_path == rel_path)
        )
    ).scalar_one()
    row.status = STATUS_DONE
    row.size_bytes = len(data)
    row.migrated_at = _utc_now()
    row.error = None
    row.attempts = int(row.attempts or 0) + 1
    await session.commit()


async def run_migration(app_db_factory) -> None:
    """执行（或续跑）本地文件 → COS 迁移，直到没有可处理的台账行或暂停。

    每批 20 条；每条单独 commit，进程中断最多丢当前文件的结果，其余已
    DONE 的行不会回退。单条失败只标记 FAILED 并继续，不会中断整轮任务。
    迁移过程绝不删除本地文件。

    **每次运行每个文件最多尝试一次**：FAILED 行留给下一次点击「继续迁移」
    重试（``attempts`` 累加，达到 ``MAX_ATTEMPTS`` 后不再被选中）。这样一次
    运行有确定的上界，失败文件不会在一轮里被连打三次而耗尽重试额度。
    """
    migration_progress.running = True
    migration_progress.current_path = None
    migration_progress.paused = False

    try:
        # 先解析一次 COS 配置并在本轮固定下来：迁移语义是把本地文件复制到
        # COS，必须与当前 storage_mode 解耦（见 _migrate_one 的说明）。
        async with app_db_factory() as session:
            base_config = await storage_service.get_storage_config(session)
            if base_config.mode != "COS":
                raise storage_service.StorageError(
                    "对象存储未启用，无法迁移；请先在后台切换到腾讯云 COS 并保存"
                )
            cos_config = base_config
            files = scan_local_files()
            await enqueue_ledger(session, files)
            await session.commit()

        # 用 id 做 keyset 分页：本轮已处理过的行（无论成功还是失败）不会被
        # 再次取到，因此「每次运行每个文件最多一次」是结构性保证，不靠内存
        # 记录排除集合。批大小固定 20，每批新建会话。
        cursor = 0
        while True:
            if _pause_requested:
                migration_progress.paused = True
                break

            async with app_db_factory() as session:
                rows = (
                    (
                        await session.execute(
                            select(StorageMigration)
                            .where(
                                StorageMigration.id > cursor,
                                or_(
                                    StorageMigration.status == STATUS_PENDING,
                                    (
                                        (StorageMigration.status == STATUS_FAILED)
                                        & (StorageMigration.attempts < MAX_ATTEMPTS)
                                    ),
                                ),
                            )
                            .order_by(StorageMigration.id)
                            .limit(BATCH_SIZE)
                        )
                    )
                    .scalars()
                    .all()
                )

                if not rows:
                    break
                # 先取成本批的纯数据 (id, rel_path) 再结束只读事务：上传可能
                # 要几十秒，不能让一条空闲的 InnoDB 读视图全程占着连接并阻塞
                # 他人的 DDL；同时避免 commit 后依赖 ORM 实例（有些 session
                # 工厂是 expire_on_commit=True，提交后读属性会再触发查询）。
                batch = [(row.id, row.rel_path) for row in rows]
                await session.commit()

                for row_id, rel_path in batch:
                    cursor = max(cursor, row_id)
                    if _pause_requested:
                        migration_progress.paused = True
                        break

                    migration_progress.current_path = rel_path
                    try:
                        await _migrate_one(session, rel_path, cos_config)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - 单条失败必须继续
                        await session.rollback()
                        message = _short_error(exc)
                        migration_progress.last_error = message
                        logger.warning("对象存储迁移失败 %s：%s", rel_path, message)
                        await _mark_failed(session, rel_path, message)

                    await asyncio.sleep(ITEM_SLEEP_SECONDS)

                if _pause_requested:
                    break

    finally:
        migration_progress.running = False
        migration_progress.current_path = None
        # 刚才可能有一批对象刚写进 COS，签名层缓存里还记着「不存在」。
        # 不清掉的话这些图要等满 2 分钟缓存 TTL 才会变成 COS 直链。
        _invalidate_signing_exists_cache()


# =============================================================================
# 清理本地文件
# =============================================================================

async def run_cleanup(app_db_factory) -> None:
    """删除「台账 DONE 且 COS 验证存在」的本地文件。

    只处理 DONE 行：不依赖扫描顺序，也不会误删尚未迁移的文件。每个文件
    删除前都重新 ``exists`` 校验一次 —— COS 对象被外部删掉时保留本地副本，
    宁可多占磁盘也不能丢掉唯一副本。
    """
    cleanup_progress.running = True
    reset_cleanup_progress()

    try:
        last_id = 0
        while True:
            async with app_db_factory() as session:
                rows = (
                    (
                        await session.execute(
                            select(StorageMigration)
                            .where(
                                StorageMigration.status == STATUS_DONE,
                                StorageMigration.id > last_id,
                            )
                            .order_by(StorageMigration.id)
                            .limit(BATCH_SIZE)
                        )
                    )
                    .scalars()
                    .all()
                )
                if not rows:
                    break
                # 先取成本批的纯数据再结束只读事务：否则「存在性校验 + 删
                # 文件」期间会一直持有 InnoDB 读视图，既可能挡住别人的 DDL
                # （AUTO_INCREMENT/加列），也让连接池被后台任务占用；同时
                # commit 后不再碰 ORM 实例（expire_on_commit=True 的工厂会
                # 因读取属性再发起一次查询）。
                batch = [(row.id, row.rel_path) for row in rows]
                await session.commit()

                for row_id, rel_path in batch:
                    last_id = row_id
                    if not _local_path(rel_path).is_file():
                        # 本地已不在磁盘（上一轮清理过或人工删掉）：把台账落到
                        # PURGED，否则下一轮迁移会拿它当"待迁移"再报找不到源文件。
                        await _mark_purged(session, rel_path)
                        continue
                    try:
                        if not await storage_service.exists(rel_path, session):
                            message = "COS 中找不到该对象，已保留本地文件"
                            cleanup_progress.failed += 1
                            cleanup_progress.last_error = message
                            logger.warning("清理跳过 %s：%s", rel_path, message)
                            continue
                        await storage_service.delete_local(rel_path)
                        # 先删本地、再落 PURGED：若两步之间进程被杀，下一轮清理
                        # 看到文件已不存在会补记 PURGED，状态不会悬空。
                        await _mark_purged(session, rel_path)
                        cleanup_progress.deleted += 1
                        cleanup_progress.remaining = max(
                            0, cleanup_progress.remaining - 1
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - 单条失败必须继续
                        cleanup_progress.failed += 1
                        cleanup_progress.last_error = f"清理失败：{str(exc)[:500]}"
                        logger.warning(
                            "清理本地文件失败 %s：%s", rel_path, cleanup_progress.last_error
                        )

                    await asyncio.sleep(ITEM_SLEEP_SECONDS)
    finally:
        # 结束时以磁盘实况校正 remaining：失败的文件仍占磁盘。
        try:
            cleanup_progress.remaining = len(scan_local_files())
        except OSError:  # pragma: no cover - 极端的目录不可读
            pass
        cleanup_progress.running = False
        _invalidate_signing_exists_cache()
