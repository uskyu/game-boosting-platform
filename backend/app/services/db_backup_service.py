"""数据库容灾备份服务：mysqldump → gzip → 腾讯云 COS。

设计要点（当前部署只有 1 个 uvicorn worker）：

- **只在 COS 落备份**：数据库备份必须离开本机，配置取自 site_settings 的
  COS 凭据；即使当前上传模式是 LOCAL，备份也不会写本地磁盘。
- **进程内单实例**：调度器每 15 秒轮询一次，``run_backup`` 用事件循环内的
  ``asyncio.Lock`` + 模块标志保证同一时刻只有一次 dump 在跑；API 层可直接
  用 ``is_running()`` 展示状态。
- **阶段之间不占连接**：读配置、插入 RUNNING、更新结果、保留清理各自使用
  独立短会话，dump 与上传期间不持有数据库连接。
- **敏感信息不入日志**：数据库密码只写进 0600 的临时 defaults-extra-file，
  通过 ``--defaults-extra-file`` 传给 mysqldump，绝不放进命令行；日志与
  错误信息都不包含数据库密码或 COS 密钥。
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import os
import re
import subprocess
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.models.database_backup import (
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    TRIGGER_MANUAL,
    TRIGGER_SCHEDULED,
    DatabaseBackup,
)
from app.models.site_setting import SiteSetting
from app.services import storage_service

logger = logging.getLogger(__name__)

# 调度间隔由 main.py 控制；这里只放业务阈值。
STALE_RUN_HOURS = 2
FAILED_RETRY_MIN_MINUTES = 5
FAILED_RETRY_MAX_MINUTES = 10
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 1440
DEFAULT_INTERVAL_MINUTES = 60
DEFAULT_RETENTION_COUNT = 48
DEFAULT_PREFIX = "db-backups"
MAX_PREFIX_LENGTH = 120
# 只允许 ASCII 字母数字与 - _ . /；首字符必须是字母或数字。避免后台存进
# 冒号/空格/星号等字符后，真正上传时才被 storage_service.normalize_key 或
# COS key 规则拒绝（那时错误发生在备份任务里，管理员看不到保存失败）。
_BACKUP_PREFIX_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
FAILED_RETENTION_DAYS = 30
FAILED_KEEP_RECENT = 200
ERROR_MESSAGE_MAX_LENGTH = 500
GZIP_COMPRESSLEVEL = 6
DUMP_CHUNK_SIZE = 64 * 1024
# 备份包可能有几十 MB，复用图片上传的 15s 超时会在慢链路上误判失败。
BACKUP_UPLOAD_TIMEOUT_SECONDS = 300.0
# mysqldump 可能因为锁等待/网络问题永久卡住；超时后强制 kill，避免备份锁
# 永远不释放、调度器从此不再跑后续备份。30 分钟远大于正常全量导出耗时。
MYSQLDUMP_TIMEOUT_SECONDS = 30 * 60

# 进程内运行锁与状态标志（单 worker 部署，不做跨进程协调）。
_RUN_LOCK = asyncio.Lock()
_RUNNING_FLAG = False


class BackupError(RuntimeError):
    """备份流程中的中文可读错误（不包含数据库密码或 COS 密钥）。"""


class DumpError(BackupError):
    """mysqldump 执行失败。"""


def _utc_now() -> datetime:
    """库内统一存 naive UTC，与项目其他 DateTime 列保持一致。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def async_session_factory():
    """取一个全新短会话的模块级入口（便于测试替换）。

    默认动态读取 ``app.db.session.async_session_factory``：测试 conftest 会
    替换那个属性，模块级直接绑定旧值会把后台任务写到真实库。测试也可以
    直接 monkeypatch 本函数来注入自己的会话工厂。
    """
    from app.db.session import async_session_factory as factory

    return factory()


def _new_session():
    """取一个全新短会话（内部统一走可替换的模块级入口）。"""
    return async_session_factory()


# =============================================================================
# 配置归一化与调度时间
# =============================================================================


def normalize_backup_prefix(value: str) -> str:
    """规范化 COS 备份目录前缀。

    - 去掉首尾空白与斜杠（反斜杠先统一成 POSIX 分隔符）；
    - 只允许 ASCII 字母数字与 ``-`` ``_`` ``.`` ``/``，且首字符必须是字母
      或数字；冒号、空格、星号等会让 storage_service.normalize_key 或 COS
      key 规则拒绝，必须在保存设置时就拦下；
    - 拒绝空值、路径穿越（``.`` / ``..``）、连续空路径段与控制字符；
    - 返回规范化的 POSIX 相对前缀，例如 ``db-backups/cn1``。
    """
    if not isinstance(value, str):
        raise ValueError("备份目录前缀不能为空")
    normalized = value.strip().replace("\\", "/").strip("/")
    if not normalized:
        raise ValueError("备份目录前缀不能为空")
    if len(normalized) > MAX_PREFIX_LENGTH:
        raise ValueError(f"备份目录前缀过长（最多 {MAX_PREFIX_LENGTH} 个字符）")
    if not _BACKUP_PREFIX_PATTERN.fullmatch(normalized):
        raise ValueError(
            "备份目录前缀只能包含英文字母、数字及 - _ . /，"
            "且必须以字母或数字开头"
        )
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("备份目录前缀不能包含空路径段或路径穿越")
    return "/".join(parts)


def _normalized_interval_minutes(setting: SiteSetting) -> int:
    """读取备份间隔并夹取到业务范围，坏数据不阻塞调度。"""
    try:
        value = int(setting.db_backup_interval_minutes)
    except (TypeError, ValueError):
        value = DEFAULT_INTERVAL_MINUTES
    return min(max(value, MIN_INTERVAL_MINUTES), MAX_INTERVAL_MINUTES)


def _normalized_retention_count(value) -> int:
    """保留份数至少为 1，避免清理把刚成功的备份也删掉。"""
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = DEFAULT_RETENTION_COUNT
    return max(1, count)


def _is_stale_started_at(started_at: datetime, now: datetime) -> bool:
    return started_at <= now - timedelta(hours=STALE_RUN_HOURS)


def compute_next_run_at(
    setting: SiteSetting, last_run: DatabaseBackup | None
) -> datetime | None:
    """计算下一次自动备份时间；None 表示当前无需自动备份。

    优先级：
    - 手动请求 ``db_backup_requested_at`` 非空：返回该时间（立即执行）；
    - 开关关闭且无手动请求：None；
    - 没有任何历史运行：返回 now（表示尽快跑第一次）；
    - 上次 FAILED：以 ``finished_at``（缺失则回退 ``started_at``）为基准，
      加 ``min(interval, 10)`` 分钟，至少 5 分钟；
    - 上次 SUCCESS：以 ``finished_at``（缺失则回退 ``started_at``）为基准，
      加 interval 分钟，避免长备份刚跑完就立刻又跑一次；
    - 上次 RUNNING 且已超过 2 小时：按 stale 失败处理；RUNNING 未过期时
      仍以 ``started_at`` 计算下一次，防止同一任务被重复触发；
    - 其他情况：``finished_at or started_at + interval``。
    """
    now = _utc_now()
    requested_at = getattr(setting, "db_backup_requested_at", None)
    if requested_at is not None:
        return requested_at

    if not bool(getattr(setting, "db_backup_enabled", False)):
        return None

    if last_run is None:
        return now

    interval_minutes = _normalized_interval_minutes(setting)
    started_at = last_run.started_at or now
    status = last_run.status

    if status == STATUS_RUNNING:
        if _is_stale_started_at(started_at, now):
            # 老 RUNNING 行说明上次进程没有收尾，按失败重试逻辑处理。
            status = STATUS_FAILED
        else:
            # 正在运行的记录不再触发新任务；返回保守的下一次时间，
            # 真正的并发保护由 _RUN_LOCK 负责。
            return started_at + timedelta(minutes=interval_minutes)

    # SUCCESS / FAILED（含 stale RUNNING）都以实际结束时间为基准：
    # 一次全量备份可能跑 7 分钟，若仍按 started_at + interval 会在一结束
    # 就立刻满足条件，形成 5 分钟间隔却连续两次备份的抖动。
    base_time = last_run.finished_at or started_at
    if status == STATUS_FAILED:
        retry_minutes = max(
            FAILED_RETRY_MIN_MINUTES,
            min(interval_minutes, FAILED_RETRY_MAX_MINUTES),
        )
        return base_time + timedelta(minutes=retry_minutes)

    return base_time + timedelta(minutes=interval_minutes)


def is_running() -> bool:
    """本进程是否正在执行一次备份（API 层用于展示/拒绝重复触发）。"""
    return _RUNNING_FLAG


# =============================================================================
# 运行记录维护
# =============================================================================


def _duration_ms(started_at: datetime | None, finished_at: datetime) -> int | None:
    if started_at is None:
        return None
    value = int((finished_at - started_at).total_seconds() * 1000)
    return max(0, min(value, 2_147_483_647))


def _error_message(exc: BaseException) -> str:
    """把异常压成单行中文可读文本并截断到 500 字。"""
    text = str(exc).strip() or exc.__class__.__name__
    text = " ".join(text.split())
    return text[:ERROR_MESSAGE_MAX_LENGTH]


async def recover_stale_runs() -> int:
    """应用启动时把所有仍 RUNNING 的记录改成 FAILED。

    单 worker 部署下，启动时进程中不可能还有备份在跑，因此**不做时间阈值**
    判断：任何遗留 RUNNING 都是上次进程异常退出留下的，必须立即收尾，否则
    API 会一直显示「备份执行中」并挡住手动触发。该函数只在 main.py 的备份
    调度任务启动时调用一次，不参与周期轮询。

    返回本次修复的记录数。数据库不可用时让异常上抛，由调用方记录。
    """
    async with _new_session() as session:
        rows = (
            (
                await session.execute(
                    select(DatabaseBackup).where(DatabaseBackup.status == STATUS_RUNNING)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return 0

        finished_at = _utc_now()
        for row in rows:
            row.status = STATUS_FAILED
            row.error_message = "备份进程中断（服务重启）"
            row.finished_at = finished_at
            row.duration_ms = _duration_ms(row.started_at, finished_at)
        await session.commit()

    logger.warning("Recovered %s stale database backup run(s)", len(rows))
    return len(rows)


async def _create_running_record(
    *, trigger: str, triggered_by: int | None, include_notifications: bool
) -> DatabaseBackup:
    """插入 RUNNING 记录并立即提交，拿到 id 后再开始耗时操作。"""
    started_at = _utc_now()
    async with _new_session() as session:
        record = DatabaseBackup(
            status=STATUS_RUNNING,
            trigger=trigger,
            started_at=started_at,
            include_notifications=include_notifications,
            triggered_by=triggered_by,
        )
        session.add(record)
        await session.commit()
        return record


async def _mark_success(
    record_id: int, *, object_key: str, size_bytes: int, started_at: datetime
) -> DatabaseBackup | None:
    finished_at = _utc_now()
    async with _new_session() as session:
        record = await session.get(DatabaseBackup, record_id)
        if record is None:
            logger.error("Database backup record %s disappeared before success update", record_id)
            return None
        record.status = STATUS_SUCCESS
        record.object_key = object_key
        record.size_bytes = size_bytes
        record.error_message = None
        record.finished_at = finished_at
        record.duration_ms = _duration_ms(started_at, finished_at)
        await session.commit()
        return record


async def _mark_failed(record_id: int, message: str) -> DatabaseBackup | None:
    finished_at = _utc_now()
    async with _new_session() as session:
        record = await session.get(DatabaseBackup, record_id)
        if record is None:
            logger.error("Database backup record %s disappeared before failure update", record_id)
            return None
        record.status = STATUS_FAILED
        record.error_message = message[:ERROR_MESSAGE_MAX_LENGTH]
        record.finished_at = finished_at
        record.duration_ms = _duration_ms(record.started_at, finished_at)
        await session.commit()
        return record


async def _load_config_snapshot() -> tuple[str, bool, int]:
    """读取一次备份配置快照：prefix / include_notifications / retention。

    行缺失或列值非法时退回默认值并记 warning；备份流程不应因一个坏前缀
    永久卡死。API 层负责保存时的业务校验。
    """
    try:
        async with _new_session() as session:
            setting = (
                await session.execute(select(SiteSetting).where(SiteSetting.id == 1))
            ).scalar_one_or_none()
            if setting is None:
                return DEFAULT_PREFIX, False, DEFAULT_RETENTION_COUNT

            try:
                prefix = normalize_backup_prefix(setting.db_backup_prefix)
            except ValueError:
                logger.warning(
                    "Invalid db_backup_prefix in site_settings; falling back to %s",
                    DEFAULT_PREFIX,
                )
                prefix = DEFAULT_PREFIX
            include_notifications = bool(setting.db_backup_include_notifications)
            retention_count = _normalized_retention_count(
                setting.db_backup_retention_count
            )
            return prefix, include_notifications, retention_count
    except Exception:  # noqa: BLE001 - 读配置失败时仍尝试备份（随后会以明确错误失败）
        logger.warning("Database backup settings lookup failed", exc_info=True)
        return DEFAULT_PREFIX, False, DEFAULT_RETENTION_COUNT


async def _load_cos_config() -> storage_service.StorageConfig | None:
    """用独立短会话读取显式 COS 配置（不跟随当前 storage_mode）。"""
    async with _new_session() as session:
        return await storage_service.get_cos_config(session)


# =============================================================================
# mysqldump 同步实现（只在 asyncio.to_thread 中执行）
# =============================================================================


def _quote_option_value(value: str) -> str:
    """把值写进 MySQL option file 的双引号字符串。

    先转义反斜杠再转义双引号；拒绝换行/回车/NUL，避免向配置文件注入新行。
    """
    if any(ch in value for ch in ("\x00", "\n", "\r")):
        raise DumpError("数据库连接配置包含非法控制字符，无法执行备份")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _write_mysql_defaults_file() -> Path:
    """按 DB_URL 生成 0600 的临时 client 配置，返回路径。调用方负责删除。

    密码只存在这个文件里，通过 ``--defaults-extra-file`` 传给 mysqldump；
    绝不进入命令行参数。文件内容也不会写日志。
    """
    try:
        url = make_url(settings.DB_URL)
    except Exception as exc:  # noqa: BLE001 - URL 解析失败等价于无法备份
        raise DumpError("数据库连接串解析失败，无法执行备份") from exc

    if url.get_backend_name() != "mysql":
        raise DumpError("当前数据库不是 MySQL，无法执行数据库备份")

    database = (url.database or "").strip()
    if not database:
        raise DumpError("数据库连接串缺少数据库名，无法执行备份")

    host = (url.host or "127.0.0.1").strip()
    port = int(url.port or 3306)
    username = url.username or ""
    password = url.password or ""

    lines = [
        "[client]",
        f"host={_quote_option_value(host)}",
        f"port={port}",
    ]
    if username:
        lines.append(f"user={_quote_option_value(username)}")
    # 密码为空时省略 password 行（本地 root 空密码场景，root 用户通常走
    # auth_socket 或直接允许空密码；显式 password="" 在部分客户端会报错）。
    if password:
        lines.append(f"password={_quote_option_value(password)}")
    lines.append("default-character-set=utf8mb4")

    fd, name = tempfile.mkstemp(prefix="db-backup-defaults-", suffix=".cnf")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
        # POSIX 上生效；Windows 开发机 chmod 能力有限，失败不影响功能。
        try:
            os.chmod(name, 0o600)
        except OSError:
            pass
        return Path(name)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def _mysqldump_base_args(defaults_path: Path) -> list[str]:
    binary = (settings.MYSQLDUMP_BIN or "").strip() or "mysqldump"
    # --defaults-extra-file 必须是 mysqldump 的第一个参数，密码才能从文件读取。
    return [
        binary,
        f"--defaults-extra-file={defaults_path}",
        "--single-transaction",
        "--quick",
        "--no-tablespaces",
        "--skip-lock-tables",
        "--hex-blob",
        "--default-character-set=utf8mb4",
        "--set-charset",
    ]


def _single_line_error(raw: bytes, limit: int = 300) -> str:
    """stderr 取第一条非空行并压成单行；不会包含命令行里的密码。"""
    text = raw.decode("utf-8", errors="replace")
    for line in text.splitlines():
        stripped = " ".join(line.split())
        if stripped:
            return stripped[:limit]
    return ""


def _append_mysqldump_output(gz_file, command: list[str]) -> None:
    """运行一次 mysqldump，把 stdout 分块写进同一个 gzip 流。

    stderr 重定向到临时文件，避免子进程写满管道阻塞；失败时只取第一行。
    """
    try:
        with tempfile.TemporaryFile(mode="wb") as stderr_file:
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=stderr_file,
                )
            except FileNotFoundError as exc:
                raise DumpError(
                    "未找到 mysqldump 命令，请确认后端镜像已安装 MySQL 客户端"
                ) from exc

            # stdout.read() 没有超时参数；用独立定时器兜底，防止 mysqldump
            # 因锁等待永久挂起，把进程内备份锁和后续调度一起卡死。
            timed_out = {"value": False}

            def _kill_on_timeout() -> None:
                if process.poll() is not None:
                    return
                timed_out["value"] = True
                try:
                    process.kill()
                except Exception:  # noqa: BLE001 - 进程可能刚好退出
                    pass

            timeout_timer = threading.Timer(MYSQLDUMP_TIMEOUT_SECONDS, _kill_on_timeout)
            timeout_timer.daemon = True
            timeout_timer.start()
            try:
                stdout = process.stdout
                if stdout is None:  # pragma: no cover - PIPE 下不会为 None
                    raise DumpError("数据库导出失败：无法读取 mysqldump 输出")
                while True:
                    chunk = stdout.read(DUMP_CHUNK_SIZE)
                    if not chunk:
                        break
                    gz_file.write(chunk)
                returncode = process.wait()
            finally:
                timeout_timer.cancel()
                if process.poll() is None:
                    process.kill()
                    process.wait()

            if timed_out["value"]:
                raise DumpError(
                    f"数据库导出超时（超过 {MYSQLDUMP_TIMEOUT_SECONDS // 60} 分钟），"
                    "已终止 mysqldump"
                )
            if returncode != 0:
                stderr_file.seek(0)
                detail = _single_line_error(stderr_file.read())
                if detail:
                    raise DumpError(
                        f"数据库导出失败（mysqldump 退出码 {returncode}）：{detail}"
                    )
                raise DumpError(f"数据库导出失败（mysqldump 退出码 {returncode}）")
    except FileNotFoundError as exc:  # 上面 Popen 已处理；这里兜底临时文件异常
        raise DumpError(
            "未找到 mysqldump 命令，请确认后端镜像已安装 MySQL 客户端"
        ) from exc


def dump_database_to_path(
    destination: str | Path, *, include_notifications: bool = False
) -> None:
    """把当前库 dump 成 gzip 文件（同步函数，调用方应放进线程池）。

    ``include_notifications=False`` 时主 dump 用 ``--ignore-table`` 跳过
    notifications 的数据，再单独追加该表的结构（``--no-data --skip-triggers``），
    这样恢复后表仍然存在，不会因为 ``alembic_version`` 已是 head 而被遗漏。
    """
    destination_path = Path(destination)
    database = (make_url(settings.DB_URL).database or "").strip()
    if not database:
        raise DumpError("数据库连接串缺少数据库名，无法执行备份")

    defaults_path = _write_mysql_defaults_file()
    try:
        base_args = _mysqldump_base_args(defaults_path)
        main_command = list(base_args)
        if not include_notifications:
            main_command.append(f"--ignore-table={database}.notifications")
        main_command.append(database)

        with gzip.open(destination_path, "wb", compresslevel=GZIP_COMPRESSLEVEL) as gz_file:
            _append_mysqldump_output(gz_file, main_command)
            if not include_notifications:
                schema_command = list(base_args)
                schema_command.extend(
                    ["--no-data", "--skip-triggers", database, "notifications"]
                )
                _append_mysqldump_output(gz_file, schema_command)
    finally:
        try:
            defaults_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to delete temporary mysqldump defaults file")


# =============================================================================
# 保留清理
# =============================================================================


async def _cleanup_success_backups(
    cos_config: storage_service.StorageConfig, retention_count: int
) -> None:
    """保留最近 ``retention_count`` 份 SUCCESS 备份，其余先删 COS 再删 DB 行。

    删除失败的记录保留在 DB 里，下一轮清理会重试；绝不先删台账再删对象。
    """
    async with _new_session() as session:
        rows = (
            await session.execute(
                select(DatabaseBackup.id, DatabaseBackup.object_key)
                .where(DatabaseBackup.status == STATUS_SUCCESS)
                .order_by(DatabaseBackup.started_at.desc(), DatabaseBackup.id.desc())
                .offset(retention_count)
            )
        ).all()
        extra = [(int(row.id), row.object_key) for row in rows]
    if not extra:
        return

    deleted_ids: list[int] = []
    for record_id, object_key in extra:
        if not object_key:
            logger.warning(
                "Skip deleting backup record %s: SUCCESS row has no object_key",
                record_id,
            )
            continue
        try:
            await storage_service.delete_for_config(object_key, cos_config)
        except Exception:  # noqa: BLE001 - 单个对象失败保留记录待下轮重试
            logger.warning(
                "Delete old backup object failed key=%s", object_key, exc_info=True
            )
            continue
        deleted_ids.append(record_id)

    if deleted_ids:
        async with _new_session() as session:
            await session.execute(
                delete(DatabaseBackup).where(DatabaseBackup.id.in_(deleted_ids))
            )
            await session.commit()
        logger.info("Retention cleanup removed %s old database backup(s)", len(deleted_ids))


async def _cleanup_old_failed_backups() -> None:
    """清理 30 天以前的 FAILED 记录，但始终保留最近 200 条失败记录。"""
    cutoff = _utc_now() - timedelta(days=FAILED_RETENTION_DAYS)
    async with _new_session() as session:
        recent_ids = (
            (
                await session.execute(
                    select(DatabaseBackup.id)
                    .where(DatabaseBackup.status == STATUS_FAILED)
                    .order_by(DatabaseBackup.started_at.desc(), DatabaseBackup.id.desc())
                    .limit(FAILED_KEEP_RECENT)
                )
            )
            .scalars()
            .all()
        )
        if len(recent_ids) < FAILED_KEEP_RECENT:
            return
        oldest_recent_id = int(recent_ids[-1])
        result = await session.execute(
            delete(DatabaseBackup).where(
                DatabaseBackup.status == STATUS_FAILED,
                DatabaseBackup.started_at < cutoff,
                DatabaseBackup.id < oldest_recent_id,
            )
        )
        await session.commit()
        removed = int(result.rowcount or 0)
    if removed:
        logger.info("Retention cleanup removed %s old failed backup record(s)", removed)


async def _cleanup_retention(
    cos_config: storage_service.StorageConfig, retention_count: int
) -> None:
    """成功后清理旧备份；任何一步失败只记日志，不影响本次 SUCCESS 状态。"""
    try:
        await _cleanup_success_backups(cos_config, retention_count)
    except Exception:  # noqa: BLE001
        logger.exception("Database backup success-record retention cleanup failed")
    try:
        await _cleanup_old_failed_backups()
    except Exception:  # noqa: BLE001
        logger.exception("Database backup failed-record retention cleanup failed")


# =============================================================================
# 对外入口
# =============================================================================


async def run_if_due() -> DatabaseBackup | None:
    """调度器每 15 秒调用：消费手动请求，或按到期时间触发一次自动备份。

    - 手动请求优先：清空 ``db_backup_requested_at`` 并提交后强制跑一次，
      即使自动备份开关是关闭的；
    - 否则仅当开关开启且 ``compute_next_run_at`` 已到期才跑；
    - 本进程已有备份在跑时直接返回 None（请求留到下一轮，不会被消费）。
    """
    if is_running():
        return None

    requested = False
    async with _new_session() as session:
        setting = (
            await session.execute(select(SiteSetting).where(SiteSetting.id == 1))
        ).scalar_one_or_none()
        if setting is None:
            return None

        requested_at = setting.db_backup_requested_at
        if requested_at is not None:
            # 消费前再检查一次：避免另一条手动触发刚启动时把请求吞掉。
            if is_running():
                return None
            setting.db_backup_requested_at = None
            await session.commit()
            requested = True
        elif not bool(setting.db_backup_enabled):
            return None
        else:
            last_run = (
                await session.execute(
                    select(DatabaseBackup)
                    .order_by(DatabaseBackup.started_at.desc(), DatabaseBackup.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            next_run_at = compute_next_run_at(setting, last_run)
            if next_run_at is None or _utc_now() < next_run_at:
                return None

    if requested:
        return await run_backup(trigger=TRIGGER_MANUAL)
    return await run_backup(trigger=TRIGGER_SCHEDULED)


async def run_backup(
    *, trigger: str, triggered_by: int | None = None
) -> DatabaseBackup | None:
    """执行一次完整备份并返回执行记录；已有备份在跑时返回 None。

    流程：配置快照 → 插入 RUNNING → 校验 COS 配置 → dump/gzip → 上传并
    HEAD 核对大小 → 落 SUCCESS → 保留清理。任何失败都把 RUNNING 记录改为
    FAILED（中文原因截断 500 字），异常记日志但不从后台调度器里抛出。
    """
    global _RUNNING_FLAG

    if _RUN_LOCK.locked():
        return None

    async with _RUN_LOCK:
        _RUNNING_FLAG = True
        started_at = _utc_now()
        record: DatabaseBackup | None = None
        tmp_path: Path | None = None
        cos_config: storage_service.StorageConfig | None = None
        retention_count = DEFAULT_RETENTION_COUNT
        normalized_trigger = (
            TRIGGER_MANUAL if trigger == TRIGGER_MANUAL else TRIGGER_SCHEDULED
        )

        try:
            prefix, include_notifications, retention_count = (
                await _load_config_snapshot()
            )
            record = await _create_running_record(
                trigger=normalized_trigger,
                triggered_by=triggered_by,
                include_notifications=include_notifications,
            )
            record_id = int(record.id)

            cos_config = await _load_cos_config()
            if cos_config is None:
                raise BackupError(
                    "请先在对象存储中配置可用的腾讯云 COS（桶、地域、密钥）"
                )

            fd, tmp_name = tempfile.mkstemp(prefix="db-backup-", suffix=".sql.gz")
            os.close(fd)
            tmp_path = Path(tmp_name)

            await asyncio.to_thread(
                dump_database_to_path,
                tmp_path,
                include_notifications=include_notifications,
            )
            size_bytes = await asyncio.to_thread(lambda: tmp_path.stat().st_size)
            if size_bytes <= 0:
                raise BackupError("数据库导出结果为空，备份失败")

            object_key = storage_service.normalize_key(
                f"{prefix}/mysql-{_utc_now():%Y%m%d-%H%M%S}.sql.gz"
            )
            payload = await asyncio.to_thread(tmp_path.read_bytes)
            await storage_service.save_bytes_for_config(
                payload,
                object_key,
                "application/gzip",
                cos_config,
                timeout_seconds=BACKUP_UPLOAD_TIMEOUT_SECONDS,
            )
            # HEAD 核对仍用默认 15s 短超时：只取对象元数据，不需要长等待。
            remote_size = await storage_service.head_size_for_config(
                object_key, cos_config
            )
            if remote_size is None:
                raise BackupError(
                    "备份上传后校验失败：COS 中找不到刚上传的备份对象"
                )
            if remote_size != size_bytes:
                raise BackupError(
                    "备份上传后校验失败：COS 对象大小不一致"
                    f"（本地 {size_bytes} 字节，远端 {remote_size} 字节）"
                )

            record = await _mark_success(
                record_id,
                object_key=object_key,
                size_bytes=size_bytes,
                started_at=started_at,
            )

            # 清理失败只影响旧备份，本次的成功状态已经落库，不能回滚。
            try:
                await _cleanup_retention(cos_config, retention_count)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Database backup retention cleanup failed")
        except asyncio.CancelledError:
            # 服务关闭时任务被取消：尽量把 RUNNING 记录收尾为 FAILED。
            if record is not None and record.status == STATUS_RUNNING:
                try:
                    await _mark_failed(record.id, "备份任务被取消（服务停止）")
                except Exception:  # noqa: BLE001
                    logger.exception("Failed to mark cancelled database backup as FAILED")
            raise
        except Exception as exc:  # noqa: BLE001 - 后台任务不能因单次失败退出
            message = _error_message(exc)
            logger.exception(
                "Database backup failed trigger=%s error=%s",
                normalized_trigger,
                message,
            )
            if record is not None:
                try:
                    record = await _mark_failed(int(record.id), message)
                except Exception:  # noqa: BLE001
                    logger.exception("Failed to mark database backup as FAILED")
            return record
        finally:
            if tmp_path is not None:
                try:
                    await asyncio.to_thread(tmp_path.unlink, missing_ok=True)
                except Exception:  # noqa: BLE001 - 清理临时文件失败只记日志
                    logger.warning("Failed to delete temporary database backup file")
            _RUNNING_FLAG = False

        return record
