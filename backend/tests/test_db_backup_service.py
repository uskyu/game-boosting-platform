"""数据库容灾备份服务层测试。

覆盖前缀规范化、下次运行时间、遗留 RUNNING 修复，以及 run_backup 的
成功/失败链路。mysqldump、COS 上传/HEAD/删除全部用 monkeypatch 替身，
不执行真实备份，也不发起任何外网请求。数据库走 conftest 的 MySQL 测试库
（``app.db.session.async_session_factory`` 已被替换，服务内部调用时导入）。
"""

import gzip
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.database_backup import (
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    TRIGGER_MANUAL,
    TRIGGER_SCHEDULED,
    DatabaseBackup,
)
from app.models.site_setting import SiteSetting
from app.services import db_backup_service, storage_service

# 仅作为 monkeypatch 返回值使用，不会真的访问 COS。
COS_CONFIG = storage_service.StorageConfig(
    mode="COS",
    secret_id="AKIDServiceTest0001",
    secret_key="ServiceTestSecretKey0001",
    bucket="game-1300000001",
    region="ap-guangzhou",
)


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_setting(
    *,
    enabled: bool,
    interval_minutes: int = 60,
    requested_at: datetime | None = None,
) -> SiteSetting:
    """构造未落库的 SiteSetting，仅供 compute_next_run_at 读取列值。"""
    setting = SiteSetting()
    setting.db_backup_enabled = enabled
    setting.db_backup_interval_minutes = interval_minutes
    setting.db_backup_requested_at = requested_at
    return setting


def _make_run(
    *,
    status: str,
    started_at: datetime,
    finished_at: datetime | None = None,
) -> DatabaseBackup:
    """构造未落库的 DatabaseBackup，仅供 compute_next_run_at 读取列值。"""
    return DatabaseBackup(
        status=status,
        trigger=TRIGGER_SCHEDULED,
        started_at=started_at,
        finished_at=finished_at,
        include_notifications=False,
    )


@pytest.fixture(autouse=True)
def _reset_running_flag():
    """进程内运行标志跨用例存活：每轮前后都确保回到空闲状态。"""
    db_backup_service._RUNNING_FLAG = False
    yield
    db_backup_service._RUNNING_FLAG = False


# =============================================================================
# 前缀规范化
# =============================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("db-backups", "db-backups"),
        ("db-backups/cn1", "db-backups/cn1"),
        ("db_backups-1", "db_backups-1"),
        # 首尾空白与斜杠会被去掉，反斜杠统一成 POSIX 分隔符
        ("  /db-backups/cn1/  ", "db-backups/cn1"),
        ("db-backups\\cn1", "db-backups/cn1"),
    ],
    ids=["plain", "nested", "underscore", "trim-slashes", "backslash"],
)
def test_normalize_backup_prefix_accepts_valid_values(raw: str, expected: str):
    assert db_backup_service.normalize_backup_prefix(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "/",
        "a//b",
        "../x",
        "db:backups",
        "db backups",
        "a" * 121,
    ],
    ids=[
        "empty",
        "slash-only",
        "empty-segment",
        "traversal",
        "colon",
        "space",
        "too-long",
    ],
)
def test_normalize_backup_prefix_rejects_invalid_values(raw: str):
    with pytest.raises(ValueError):
        db_backup_service.normalize_backup_prefix(raw)


# =============================================================================
# 下次运行时间
# =============================================================================


def test_compute_next_run_at_returns_none_when_disabled():
    setting = _make_setting(enabled=False)
    last_run = _make_run(
        status=STATUS_SUCCESS,
        started_at=_naive_utc_now() - timedelta(hours=2),
        finished_at=_naive_utc_now() - timedelta(hours=1),
    )
    assert db_backup_service.compute_next_run_at(setting, last_run) is None


def test_compute_next_run_at_returns_requested_at_verbatim():
    """手动请求优先：即使自动开关关闭也直接返回请求时间。"""
    requested_at = datetime(2026, 11, 26, 10, 0, 0)
    setting = _make_setting(enabled=False, requested_at=requested_at)
    result = db_backup_service.compute_next_run_at(setting, None)
    assert result == requested_at
    assert result is not None and result.tzinfo is None


def test_compute_next_run_at_without_history_is_now():
    setting = _make_setting(enabled=True)
    before = _naive_utc_now()
    result = db_backup_service.compute_next_run_at(setting, None)
    after = _naive_utc_now()

    assert result is not None
    assert result.tzinfo is None
    # 没有历史记录时应「尽快执行」，时间点等于调用时的 naive UTC now。
    assert before - timedelta(seconds=1) <= result <= after + timedelta(seconds=1)


def test_compute_next_run_at_after_success_uses_finished_at():
    """SUCCESS 以实际结束时间为基准，避免长备份刚结束就又触发一次。"""
    started_at = datetime(2026, 11, 26, 10, 0, 0)
    finished_at = started_at + timedelta(minutes=7)
    setting = _make_setting(enabled=True, interval_minutes=60)
    last_run = _make_run(
        status=STATUS_SUCCESS, started_at=started_at, finished_at=finished_at
    )

    result = db_backup_service.compute_next_run_at(setting, last_run)
    assert result == finished_at + timedelta(minutes=60)
    # 必须不是 started_at + interval（11:00），而是 11:07。
    assert result != started_at + timedelta(minutes=60)


@pytest.mark.parametrize(("interval_minutes", "retry_minutes"), [(60, 10), (5, 5)])
def test_compute_next_run_at_after_failed_uses_finished_at_and_retry_cap(
    interval_minutes: int, retry_minutes: int
):
    """FAILED 以 finished_at 为基准 + min(interval, 10)，且至少 5 分钟。"""
    started_at = datetime(2026, 11, 26, 10, 0, 0)
    finished_at = started_at + timedelta(minutes=3)
    setting = _make_setting(enabled=True, interval_minutes=interval_minutes)
    last_run = _make_run(
        status=STATUS_FAILED, started_at=started_at, finished_at=finished_at
    )

    result = db_backup_service.compute_next_run_at(setting, last_run)
    assert result == finished_at + timedelta(minutes=retry_minutes)
    # 不能退化成 started_at 基准
    assert result != started_at + timedelta(minutes=retry_minutes) or (
        finished_at == started_at
    )


def test_compute_next_run_at_running_not_stale_waits_started_at_interval():
    started_at = _naive_utc_now() - timedelta(minutes=10)
    setting = _make_setting(enabled=True, interval_minutes=60)
    last_run = _make_run(status=STATUS_RUNNING, started_at=started_at, finished_at=None)

    result = db_backup_service.compute_next_run_at(setting, last_run)
    assert result == started_at + timedelta(minutes=60)


def test_compute_next_run_at_stale_running_falls_back_to_failed_retry():
    """RUNNING 超过 2 小时：按失败重试基准，而不是 started_at + 60 分钟。"""
    started_at = _naive_utc_now() - timedelta(hours=3)
    setting = _make_setting(enabled=True, interval_minutes=60)
    last_run = _make_run(status=STATUS_RUNNING, started_at=started_at, finished_at=None)

    result = db_backup_service.compute_next_run_at(setting, last_run)
    assert result == started_at + timedelta(minutes=10)
    assert result != started_at + timedelta(minutes=60)


# =============================================================================
# 启动时修复遗留 RUNNING
# =============================================================================


async def test_recover_stale_runs_marks_running_as_failed(db_session):
    record = DatabaseBackup(
        status=STATUS_RUNNING,
        trigger=TRIGGER_MANUAL,
        started_at=_naive_utc_now() - timedelta(minutes=5),
        include_notifications=False,
    )
    db_session.add(record)
    await db_session.commit()

    recovered = await db_backup_service.recover_stale_runs()
    assert recovered == 1

    await db_session.refresh(record)
    assert record.status == STATUS_FAILED
    assert record.finished_at is not None
    assert record.finished_at.tzinfo is None
    assert "服务重启" in (record.error_message or "")


# =============================================================================
# run_backup 成功链路
# =============================================================================


async def test_run_backup_success_uploads_and_cleans_old_backup(
    db_session, monkeypatch
):
    """全量替身链路：新备份 SUCCESS、写 COS、按保留份数清理旧记录/对象。"""
    # 站点设置：前缀 + 保留 1 份 + 包含 notifications
    setting = (
        await db_session.execute(select(SiteSetting).where(SiteSetting.id == 1))
    ).scalar_one()
    setting.db_backup_prefix = "db-backups/cn1"
    setting.db_backup_retention_count = 1
    setting.db_backup_include_notifications = True
    await db_session.commit()

    old_object_key = "db-backups/cn1/mysql-old.sql.gz"
    old_record = DatabaseBackup(
        status=STATUS_SUCCESS,
        trigger=TRIGGER_SCHEDULED,
        object_key=old_object_key,
        size_bytes=10,
        started_at=_naive_utc_now() - timedelta(hours=2),
        finished_at=_naive_utc_now() - timedelta(hours=1, minutes=55),
        include_notifications=False,
    )
    db_session.add(old_record)
    await db_session.commit()

    async def _load_cos_config():
        return COS_CONFIG

    def _fake_dump(destination, *, include_notifications: bool = False) -> None:
        with gzip.open(destination, "wb") as handle:
            handle.write(b"fake mysql dump for service test")

    saved: dict = {}
    deleted_keys: list[str] = []

    async def _save_bytes(data, key, content_type, config, timeout_seconds=None):
        saved.update(
            data=data,
            key=key,
            content_type=content_type,
            config=config,
            timeout_seconds=timeout_seconds,
        )

    async def _head_size(key, config):
        return len(saved["data"])

    async def _delete_for_config(key, config):
        deleted_keys.append(key)

    monkeypatch.setattr(db_backup_service, "_load_cos_config", _load_cos_config)
    monkeypatch.setattr(db_backup_service, "dump_database_to_path", _fake_dump)
    monkeypatch.setattr(storage_service, "save_bytes_for_config", _save_bytes)
    monkeypatch.setattr(storage_service, "head_size_for_config", _head_size)
    monkeypatch.setattr(storage_service, "delete_for_config", _delete_for_config)

    record = await db_backup_service.run_backup(trigger=TRIGGER_MANUAL)

    assert record is not None
    assert record.status == STATUS_SUCCESS
    assert record.trigger == TRIGGER_MANUAL
    assert record.object_key is not None
    assert record.object_key.startswith("db-backups/cn1/mysql-")
    assert record.object_key.endswith(".sql.gz")
    assert record.size_bytes is not None and record.size_bytes > 0
    assert record.include_notifications is True
    assert record.error_message is None
    assert record.finished_at is not None

    # 上传字节与对象 key 对得上，且确实是 gzip 内容。
    assert saved["key"] == record.object_key
    assert saved["data"].startswith(b"\x1f\x8b")
    assert len(saved["data"]) == record.size_bytes
    assert saved["config"] is COS_CONFIG
    assert saved["timeout_seconds"] == db_backup_service.BACKUP_UPLOAD_TIMEOUT_SECONDS

    # 保留 1 份：旧对象被删除，旧台账行被清理，只留下本次成功记录。
    assert deleted_keys == [old_object_key]
    await db_session.commit()
    db_session.expire_all()
    remaining = (await db_session.execute(select(DatabaseBackup))).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].id == record.id

    assert db_backup_service.is_running() is False


# =============================================================================
# run_backup 失败链路
# =============================================================================


async def test_run_backup_failure_records_failed_and_resets_running(db_session, monkeypatch):
    """dump 抛 DumpError：RUNNING 记录收尾为 FAILED，运行标志恢复 False。"""
    async def _load_cos_config():
        return COS_CONFIG

    def _failing_dump(destination, *, include_notifications: bool = False) -> None:
        raise db_backup_service.DumpError("模拟 mysqldump 导出失败")

    monkeypatch.setattr(db_backup_service, "_load_cos_config", _load_cos_config)
    monkeypatch.setattr(db_backup_service, "dump_database_to_path", _failing_dump)

    record = await db_backup_service.run_backup(trigger=TRIGGER_MANUAL)

    assert record is not None
    assert record.status == STATUS_FAILED
    assert record.object_key is None
    assert record.size_bytes is None
    assert record.finished_at is not None
    assert record.error_message is not None and record.error_message.strip()
    assert "模拟 mysqldump 导出失败" in record.error_message
    assert db_backup_service.is_running() is False


def test_mysqldump_tls_args_follow_client_type(monkeypatch):
    """MariaDB 客户端连 MySQL 自签证书会校验证书失败，必须加 --skip-ssl；
    MySQL 8 客户端没有该选项，不能加。"""
    db_backup_service._MYSQLDUMP_TLS_ARGS_CACHE.clear()

    def _fake_version(command, **kwargs):
        binary = command[0] if isinstance(command, (list, tuple)) else command
        if binary == "mariadb-dump":
            return types.SimpleNamespace(stdout="mysqldump from 11.8.9-MariaDB")
        return types.SimpleNamespace(stdout="mysqldump Ver 8.0.43 for Win64 (MySQL)")

    monkeypatch.setattr(db_backup_service.subprocess, "run", _fake_version)
    monkeypatch.setattr(db_backup_service.settings, "MYSQLDUMP_BIN", "mariadb-dump")
    args = db_backup_service._mysqldump_base_args(Path("/tmp/x.cnf"))
    assert "--skip-ssl" in args

    db_backup_service._MYSQLDUMP_TLS_ARGS_CACHE.clear()
    monkeypatch.setattr(db_backup_service.settings, "MYSQLDUMP_BIN", "mysqldump")
    args = db_backup_service._mysqldump_base_args(Path("/tmp/x.cnf"))
    assert "--skip-ssl" not in args
