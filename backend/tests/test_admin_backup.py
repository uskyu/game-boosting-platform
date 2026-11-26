"""管理员「数据库容灾备份」API 测试。

数据库使用 conftest 的 MySQL 测试库；mysqldump 与 COS 全部用 monkeypatch
替身，绝不做真实备份、也不发起任何 COS 网络请求。所有替换由 pytest 的
``monkeypatch`` fixture 在用例结束后自动还原。
"""

import logging
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.database_backup import (
    STATUS_FAILED,
    STATUS_SUCCESS,
    TRIGGER_MANUAL,
    TRIGGER_SCHEDULED,
    DatabaseBackup,
)
from app.models.site_setting import SiteSetting
from app.services import db_backup_service, storage_service
from tests.conftest import auth_header

ADMIN_SETTINGS = "/admin/backup/settings"
ADMIN_RUN = "/admin/backup/run"
ADMIN_RUNS = "/admin/backup/runs"

# 仅用于 monkeypatch 的 COS 配置替身：不会真的被任何客户端使用。
COS_CONFIG = storage_service.StorageConfig(
    mode="COS",
    secret_id="AKIDTestSecretId0001",
    secret_key="TestSecretKey0001",
    bucket="game-1300000000",
    region="ap-guangzhou",
)


def _payload(**overrides) -> dict:
    payload = {
        "enabled": False,
        "interval_minutes": 60,
        "retention_count": 48,
        "prefix": "db-backups",
        "include_notifications": False,
    }
    payload.update(overrides)
    return payload


def _make_run(
    *,
    status: str = STATUS_SUCCESS,
    trigger: str = TRIGGER_SCHEDULED,
    started_at: datetime,
    finished_at: datetime | None = None,
    object_key: str | None = None,
    size_bytes: int | None = None,
) -> DatabaseBackup:
    return DatabaseBackup(
        status=status,
        trigger=trigger,
        object_key=object_key,
        size_bytes=size_bytes,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=1000 if finished_at else None,
        error_message=None,
        include_notifications=False,
    )


async def test_non_admin_is_forbidden(client: AsyncClient, registered_user: dict):
    """普通登录用户访问全部备份接口都被 403 挡住。"""
    headers = auth_header(registered_user)
    for method, url, kwargs in (
        ("get", ADMIN_SETTINGS, {}),
        ("put", ADMIN_SETTINGS, {"json": _payload()}),
        ("post", ADMIN_RUN, {}),
        ("get", ADMIN_RUNS, {}),
    ):
        response = await getattr(client, method)(url, headers=headers, **kwargs)
        assert response.status_code == 403


async def test_get_settings_returns_defaults(
    client: AsyncClient, admin_user: dict
):
    """全新站点：默认关闭、60 分钟、保留 48 份、无 COS、无历史。"""
    response = await client.get(ADMIN_SETTINGS, headers=auth_header(admin_user))
    assert response.status_code == 200
    data = response.json()

    assert data["enabled"] is False
    assert data["interval_minutes"] == 60
    assert data["retention_count"] == 48
    assert data["prefix"] == "db-backups"
    assert data["include_notifications"] is False
    assert data["cos_configured"] is False
    assert data["storage_mode"] == "LOCAL"
    assert data["running"] is False
    assert data["last_backup"] is None
    assert data["next_run_at"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"interval_minutes": 4},
        {"interval_minutes": 1441},
        {"retention_count": 0},
        {"retention_count": 501},
        {"prefix": ".."},
        {"prefix": "a//b"},
    ],
    ids=[
        "interval-below-min",
        "interval-above-max",
        "retention-zero",
        "retention-too-large",
        "prefix-traversal",
        "prefix-empty-segment",
    ],
)
async def test_put_rejects_invalid_values(
    client: AsyncClient, admin_user: dict, overrides: dict
):
    response = await client.put(
        ADMIN_SETTINGS,
        json=_payload(**overrides),
        headers=auth_header(admin_user),
    )
    assert response.status_code == 422
    if "prefix" in overrides:
        # 前缀由 db_backup_service.normalize_backup_prefix 校验，返回中文原因。
        detail = response.json()["detail"]
        assert isinstance(detail, str)
        assert "备份目录前缀" in detail


async def test_put_saves_settings_and_get_reads_back(
    client: AsyncClient, admin_user: dict
):
    """合法保存（含前缀规范化）后，GET 与 PUT 响应一致。"""
    payload = _payload(
        interval_minutes=120,
        retention_count=5,
        prefix="/db-backups/prod/",
        include_notifications=True,
    )
    response = await client.put(
        ADMIN_SETTINGS, json=payload, headers=auth_header(admin_user)
    )
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is False
    assert data["interval_minutes"] == 120
    assert data["retention_count"] == 5
    assert data["prefix"] == "db-backups/prod"
    assert data["include_notifications"] is True

    get_response = await client.get(ADMIN_SETTINGS, headers=auth_header(admin_user))
    assert get_response.status_code == 200
    readback = get_response.json()
    assert readback["interval_minutes"] == 120
    assert readback["retention_count"] == 5
    assert readback["prefix"] == "db-backups/prod"
    assert readback["include_notifications"] is True


async def test_put_enable_requires_cos_config(
    client: AsyncClient, admin_user: dict
):
    """未配置 COS 时不允许打开自动备份。"""
    response = await client.put(
        ADMIN_SETTINGS,
        json=_payload(enabled=True),
        headers=auth_header(admin_user),
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "COS" in detail
    assert "后再启用备份" in detail


async def test_put_enable_succeeds_when_cos_configured(
    client: AsyncClient, admin_user: dict, monkeypatch
):
    async def _get_cos_config(_db):
        return COS_CONFIG

    monkeypatch.setattr(storage_service, "get_cos_config", _get_cos_config)

    response = await client.put(
        ADMIN_SETTINGS,
        json=_payload(enabled=True),
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is True
    assert data["cos_configured"] is True
    # 没有历史记录且已启用：next_run_at = 尽快执行，序列化为 UTC Z。
    assert data["next_run_at"] is not None
    assert data["next_run_at"].endswith("Z")


async def test_run_requires_cos_config(client: AsyncClient, admin_user: dict):
    response = await client.post(ADMIN_RUN, headers=auth_header(admin_user))
    assert response.status_code == 400
    assert "COS" in response.json()["detail"]


async def test_run_conflicts_when_backup_is_running(
    client: AsyncClient, admin_user: dict, monkeypatch
):
    monkeypatch.setattr(db_backup_service, "is_running", lambda: True)

    response = await client.post(ADMIN_RUN, headers=auth_header(admin_user))
    assert response.status_code == 409
    assert "已有备份正在执行" in response.json()["detail"]


async def test_run_queues_request_and_reports_running(
    client: AsyncClient, admin_user: dict, db_session, monkeypatch
):
    async def _get_cos_config(_db):
        return COS_CONFIG

    monkeypatch.setattr(storage_service, "get_cos_config", _get_cos_config)

    response = await client.post(ADMIN_RUN, headers=auth_header(admin_user))
    assert response.status_code == 200
    assert response.json() == {"requested": True, "running": True}

    requested_at = (
        await db_session.execute(
            select(SiteSetting.db_backup_requested_at).where(SiteSetting.id == 1)
        )
    ).scalar_one()
    assert requested_at is not None

    # 调度器还没消费请求时，前端轮询必须仍看到 running=true。
    settings_response = await client.get(
        ADMIN_SETTINGS, headers=auth_header(admin_user)
    )
    data = settings_response.json()
    assert data["running"] is True
    assert data["next_run_at"] is not None
    assert data["next_run_at"].endswith("Z")


async def test_runs_pagination_descending_and_utc_dates(
    client: AsyncClient, admin_user: dict, db_session
):
    base = datetime(2026, 11, 26, 10, 0, 0)
    records = []
    for index in range(3):
        record = _make_run(
            started_at=base + timedelta(minutes=index),
            finished_at=base + timedelta(minutes=index, seconds=1),
            object_key=f"db-backups/mysql-{index}.sql.gz",
            size_bytes=100 + index,
            trigger=TRIGGER_MANUAL if index == 2 else TRIGGER_SCHEDULED,
        )
        db_session.add(record)
        records.append(record)
    await db_session.commit()

    response = await client.get(
        f"{ADMIN_RUNS}?page=1&page_size=2",
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["page"] == 1
    assert data["page_size"] == 2
    assert len(data["items"]) == 2
    # started_at 倒序：最新两条
    assert [item["id"] for item in data["items"]] == [
        records[2].id,
        records[1].id,
    ]
    assert data["items"][0]["started_at"].endswith("Z")
    assert data["items"][0]["finished_at"].endswith("Z")

    second_page = await client.get(
        f"{ADMIN_RUNS}?page=2&page_size=2",
        headers=auth_header(admin_user),
    )
    assert second_page.status_code == 200
    page_two = second_page.json()
    assert page_two["total"] == 3
    assert [item["id"] for item in page_two["items"]] == [records[0].id]

    # GET settings 的 last_backup 内层 datetime 也要带 Z。
    settings_response = await client.get(
        ADMIN_SETTINGS, headers=auth_header(admin_user)
    )
    last_backup = settings_response.json()["last_backup"]
    assert last_backup["id"] == records[2].id
    assert last_backup["started_at"].endswith("Z")
    assert last_backup["finished_at"].endswith("Z")


async def test_download_returns_gzip_bytes_and_basename(
    client: AsyncClient,
    admin_user: dict,
    db_session,
    monkeypatch,
    caplog,
):
    caplog.set_level(logging.INFO, logger="app.api.endpoints.admin_backup")
    record = _make_run(
        started_at=datetime(2026, 11, 26, 12, 0, 0),
        finished_at=datetime(2026, 11, 26, 12, 1, 0),
        object_key="db-backups/mysql-20261126-120000.sql.gz",
        size_bytes=6,
    )
    db_session.add(record)
    await db_session.commit()

    async def _get_cos_config(_db):
        return COS_CONFIG

    async def _read_bytes(key, config, **kwargs):
        assert kwargs.get("timeout_seconds") == 300
        assert key == "db-backups/mysql-20261126-120000.sql.gz"
        assert config is COS_CONFIG
        return b"gzdata", "application/gzip"

    monkeypatch.setattr(storage_service, "get_cos_config", _get_cos_config)
    monkeypatch.setattr(storage_service, "read_bytes_for_config", _read_bytes)

    response = await client.get(
        f"{ADMIN_RUNS}/{record.id}/download",
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
    assert response.content == b"gzdata"
    assert response.headers["content-type"] == "application/gzip"

    disposition = response.headers["content-disposition"]
    assert disposition == (
        'attachment; filename="mysql-20261126-120000.sql.gz"'
    )

    # 审计日志：管理员 id / 记录 id / object_key / 字节数，且不含备份内容。
    audit_text = " ".join(record_.getMessage() for record_ in caplog.records)
    assert f"admin_id={admin_user['user']['id']}" in audit_text
    assert f"run_id={record.id}" in audit_text
    assert record.object_key in audit_text
    assert "size_bytes=6" in audit_text
    assert "gzdata" not in audit_text


async def test_download_rejects_non_success_record(
    client: AsyncClient, admin_user: dict, db_session
):
    """失败记录即使有 object_key 也不能下载。"""
    record = _make_run(
        status=STATUS_FAILED,
        started_at=datetime(2026, 11, 26, 9, 0, 0),
        finished_at=datetime(2026, 11, 26, 9, 1, 0),
        object_key="db-backups/mysql-failed.sql.gz",
    )
    db_session.add(record)
    await db_session.commit()

    response = await client.get(
        f"{ADMIN_RUNS}/{record.id}/download",
        headers=auth_header(admin_user),
    )
    assert response.status_code == 404
    assert "备份记录不存在" in response.json()["detail"]


async def test_download_missing_object_returns_404(
    client: AsyncClient, admin_user: dict, db_session, monkeypatch
):
    record = _make_run(
        started_at=datetime(2026, 11, 26, 12, 0, 0),
        finished_at=datetime(2026, 11, 26, 12, 1, 0),
        object_key="db-backups/mysql-gone.sql.gz",
        size_bytes=10,
    )
    db_session.add(record)
    await db_session.commit()

    async def _get_cos_config(_db):
        return COS_CONFIG

    async def _read_bytes(_key, _config, **_kwargs):
        return None

    monkeypatch.setattr(storage_service, "get_cos_config", _get_cos_config)
    monkeypatch.setattr(storage_service, "read_bytes_for_config", _read_bytes)

    response = await client.get(
        f"{ADMIN_RUNS}/{record.id}/download",
        headers=auth_header(admin_user),
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "备份对象不存在或已被清理"


async def test_download_requires_cos_config(
    client: AsyncClient, admin_user: dict, db_session
):
    """历史记录存在但 COS 配置已被移除：400 + 中文提示。"""
    record = _make_run(
        started_at=datetime(2026, 11, 26, 12, 0, 0),
        finished_at=datetime(2026, 11, 26, 12, 1, 0),
        object_key="db-backups/mysql-20261126-120000.sql.gz",
    )
    db_session.add(record)
    await db_session.commit()

    response = await client.get(
        f"{ADMIN_RUNS}/{record.id}/download",
        headers=auth_header(admin_user),
    )
    assert response.status_code == 400
    assert "COS" in response.json()["detail"]
