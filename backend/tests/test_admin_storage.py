"""后台对象存储（腾讯云 COS）测试：设置脱敏、切换校验、迁移与清理。

全部 storage_service 网络操作都被 monkeypatch 成内存替身，测试不触碰任何
真实 COS：``save_bytes`` / ``exists`` / ``test_cos_connection`` / ``delete_local``
均按需替换。数据库仍是 conftest 的真实 MySQL 测试库。
"""

import asyncio
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.security import decrypt_text, encrypt_text
from app.models.site_setting import SiteSetting
from app.models.storage_migration import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    StorageMigration,
)
from app.services import storage_service, storage_migration
from tests.conftest import _session_factory, auth_header

ADMIN_SETTINGS = "/admin/storage/settings"
ADMIN_TEST = "/admin/storage/test"
ADMIN_MIGRATE = "/admin/storage/migrate"
ADMIN_MIGRATE_PAUSE = "/admin/storage/migrate/pause"
ADMIN_MIGRATE_STATUS = "/admin/storage/migrate/status"
ADMIN_CLEANUP = "/admin/storage/cleanup-local"
ADMIN_CLEANUP_STATUS = "/admin/storage/cleanup/status"

SECRET_ID = "AKIDExampleSecretId0001Cj"
SECRET_KEY = "ExampleSecretKeyPlaintextEJ"
BUCKET = "game-1314124236"
REGION = "ap-chongqing"

# 迁移任务按显式配置写入，测试里统一固定成这份 COS 配置替身
_COS_CONFIG_FOR_TESTS = storage_service.StorageConfig(
    mode="COS",
    secret_id=SECRET_ID,
    secret_key=SECRET_KEY,
    bucket=BUCKET,
    region=REGION,
    cdn_domain=None,
)


@pytest.fixture(autouse=True)
def _reset_storage_state():
    """进程内配置缓存与任务状态跨用例存活，每轮前后都清空。"""
    storage_service.invalidate_storage_config_cache()
    yield
    storage_service.invalidate_storage_config_cache()


async def _seed_setting(db_session, **values) -> SiteSetting:
    """写 site_settings 单行：默认 LOCAL，可覆盖存储列。"""
    result = await db_session.execute(select(SiteSetting).where(SiteSetting.id == 1))
    setting = result.scalar_one_or_none()
    if setting is None:
        setting = SiteSetting(id=1, site_name="游戏服务平台")
        db_session.add(setting)
    for key, value in values.items():
        setattr(setting, key, value)
    await db_session.commit()
    await db_session.refresh(setting)
    return setting


def _failing_probe(*_args, **_kwargs):
    async def _probe(_cfg):
        return False, "COS 连接失败：签名校验不通过"

    return _probe


async def test_get_settings_never_returns_plaintext_secrets(
    client: AsyncClient, admin_user: dict, db_session
):
    """GET 只回掩码；明文密钥绝不出现在响应体里。"""
    await _seed_setting(
        db_session,
        cos_secret_id=encrypt_text(SECRET_ID),
        cos_secret_key=encrypt_text(SECRET_KEY),
        cos_bucket=BUCKET,
        cos_region=REGION,
        cos_cdn_domain="cdn.example.com",
    )

    response = await client.get(ADMIN_SETTINGS, headers=auth_header(admin_user))
    assert response.status_code == 200
    data = response.json()

    assert data["storage_mode"] == "LOCAL"
    assert data["cos_bucket"] == BUCKET
    assert data["cos_region"] == REGION
    assert data["cos_cdn_domain"] == "cdn.example.com"
    assert data["cos_configured"] is True
    # 掩码形如 AKID****Cj / ****EJ
    assert data["cos_secret_id_masked"] == "AKID****Cj"
    assert data["cos_secret_key_masked"] == "****EJ"
    # 原始明文不能出现在任何字段里
    body = response.text
    assert SECRET_ID not in body
    assert SECRET_KEY not in body


async def test_get_settings_reports_not_configured_when_columns_are_null(
    client: AsyncClient, admin_user: dict
):
    """全新的站点设置：无凭据、cos_configured=False、掩码为 None。"""
    response = await client.get(ADMIN_SETTINGS, headers=auth_header(admin_user))
    assert response.status_code == 200
    data = response.json()
    assert data["storage_mode"] == "LOCAL"
    assert data["cos_secret_id_masked"] is None
    assert data["cos_secret_key_masked"] is None
    assert data["cos_configured"] is False
    assert data["cos_bucket"] is None
    assert data["cos_region"] is None


async def test_put_with_keep_sentinel_preserves_encrypted_secret(
    client: AsyncClient, admin_user: dict, db_session
):
    """``__KEEP__`` 不动已存密文：库里仍是密文，不是明文。"""
    original_cipher_id = encrypt_text(SECRET_ID)
    original_cipher_key = encrypt_text(SECRET_KEY)
    await _seed_setting(
        db_session,
        cos_secret_id=original_cipher_id,
        cos_secret_key=original_cipher_key,
        cos_bucket=BUCKET,
        cos_region=REGION,
    )

    response = await client.put(
        ADMIN_SETTINGS,
        json={
            "storage_mode": "LOCAL",
            "cos_secret_id": "__KEEP__",
            "cos_secret_key": "__KEEP__",
            # 其他字段照常保存（换桶名不该要求重输密钥）
            "cos_bucket": "game-1314124237",
            "cos_cdn_domain": "https://cdn.example.com",
        },
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
    assert response.json()["cos_bucket"] == "game-1314124237"
    # 非密钥字段也被归一：scheme 被 storage_service 的读取侧处理，这里只落原值
    assert response.json()["cos_cdn_domain"] == "https://cdn.example.com"

    raw = (
        await db_session.execute(select(SiteSetting.cos_secret_id, SiteSetting.cos_secret_key))
    ).one()
    stored_id, stored_key = raw
    assert stored_id == original_cipher_id
    assert stored_key == original_cipher_key
    # 落库值必须是密文而不是明文
    assert stored_id != SECRET_ID
    assert stored_key != SECRET_KEY
    # 且仍能解密回原值（密钥没有被换成掩码字符串）
    assert decrypt_text(stored_id) == SECRET_ID
    assert decrypt_text(stored_key) == SECRET_KEY


async def test_put_switching_to_cos_with_failing_probe_returns_400_and_keeps_local(
    client: AsyncClient, admin_user: dict, db_session, monkeypatch
):
    """切 COS 前先探测；失败 400 + 中文原因，DB 里 storage_mode 仍是 LOCAL。"""

    async def _fail(_cfg):
        return False, "COS 连接失败：SecretId 无效"

    monkeypatch.setattr(storage_service, "test_cos_connection", _fail)

    response = await client.put(
        ADMIN_SETTINGS,
        json={
            "storage_mode": "COS",
            "cos_secret_id": SECRET_ID,
            "cos_secret_key": SECRET_KEY,
            "cos_bucket": BUCKET,
            "cos_region": REGION,
        },
        headers=auth_header(admin_user),
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "COS" in detail
    assert "SecretId 无效" in detail

    storage_mode = (
        await db_session.execute(select(SiteSetting.storage_mode).where(SiteSetting.id == 1))
    ).scalar_one()
    assert storage_mode == "LOCAL"
    # 密钥也不该落库：整次保存被拒绝
    stored = (
        await db_session.execute(select(SiteSetting.cos_secret_id).where(SiteSetting.id == 1))
    ).scalar_one()
    assert stored is None


async def test_put_switching_to_cos_with_passing_probe_persists_cos(
    client: AsyncClient, admin_user: dict, db_session, monkeypatch
):
    """探测通过后 storage_mode 落库为 COS，密钥以密文保存。"""
    captured: list = []

    async def _pass(cfg):
        captured.append(cfg)
        return True, "COS 连接成功，读写与删除均正常"

    monkeypatch.setattr(storage_service, "test_cos_connection", _pass)

    response = await client.put(
        ADMIN_SETTINGS,
        json={
            "storage_mode": "COS",
            "cos_secret_id": SECRET_ID,
            "cos_secret_key": SECRET_KEY,
            "cos_bucket": BUCKET,
            "cos_region": REGION,
        },
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["storage_mode"] == "COS"
    assert data["cos_configured"] is True
    assert data["cos_secret_id_masked"] == "AKID****Cj"

    row = (
        await db_session.execute(
            select(
                SiteSetting.storage_mode,
                SiteSetting.cos_secret_id,
                SiteSetting.cos_bucket,
                SiteSetting.cos_region,
            ).where(SiteSetting.id == 1)
        )
    ).one()
    assert row[0] == "COS"
    assert row[1] != SECRET_ID  # 密文
    assert decrypt_text(row[1]) == SECRET_ID
    assert row[2] == BUCKET
    assert row[3] == REGION
    # 探测用的是本次提交的新凭据
    assert captured and captured[0].secret_id == SECRET_ID
    assert captured[0].bucket == BUCKET


async def test_post_test_falls_back_to_saved_credentials(
    client: AsyncClient, admin_user: dict, db_session, monkeypatch
):
    """请求体不带凭据时，用已保存（解密后）的配置去探测。"""
    await _seed_setting(
        db_session,
        cos_secret_id=encrypt_text(SECRET_ID),
        cos_secret_key=encrypt_text(SECRET_KEY),
        cos_bucket=BUCKET,
        cos_region=REGION,
    )

    captured: list = []

    async def _pass(cfg):
        captured.append(cfg)
        return True, "COS 连接成功，读写与删除均正常"

    monkeypatch.setattr(storage_service, "test_cos_connection", _pass)

    response = await client.post(ADMIN_TEST, json={}, headers=auth_header(admin_user))
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "连接成功" in data["message"]

    cfg = captured[0]
    assert cfg.secret_id == SECRET_ID
    assert cfg.secret_key == SECRET_KEY
    assert cfg.bucket == BUCKET
    assert cfg.region == REGION


async def test_post_test_reports_incomplete_config_without_calling_upstream(
    client: AsyncClient, admin_user: dict, monkeypatch
):
    """没有任何已保存凭据时不发起网络调用，直接返回中文提示。"""
    called = False

    async def _probe(_cfg):
        nonlocal called
        called = True
        return True, "ok"

    monkeypatch.setattr(storage_service, "test_cos_connection", _probe)

    response = await client.post(ADMIN_TEST, json={}, headers=auth_header(admin_user))
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert "SecretId" in data["message"]
    assert called is False


async def test_migrate_is_refused_while_storage_mode_is_local(
    client: AsyncClient, admin_user: dict
):
    response = await client.post(ADMIN_MIGRATE, headers=auth_header(admin_user))
    assert response.status_code == 400
    assert "COS" in response.json()["detail"]

    # 拒绝时不应留下正在运行的任务句柄
    from app.api.endpoints import admin_storage

    assert admin_storage.migration_job.running is False


async def test_migrate_status_shape(
    client: AsyncClient, admin_user: dict
):
    response = await client.get(ADMIN_MIGRATE_STATUS, headers=auth_header(admin_user))
    assert response.status_code == 200
    data = response.json()
    for field in (
        "total",
        "done",
        "failed",
        "pending",
        "running",
        "paused",
        "current_path",
        "last_error",
        "local_files",
        "local_bytes",
    ):
        assert field in data
    assert data["running"] is False


async def test_cleanup_is_refused_while_ledger_has_non_done_rows(
    client: AsyncClient, admin_user: dict, db_session, monkeypatch, tmp_path
):
    """台账还有 PENDING/FAILED 时拒绝清理，并在中文提示里给出数量。"""
    from app.core import config

    monkeypatch.setattr(config.settings, "UPLOAD_DIR", str(tmp_path))
    (tmp_path / "orders").mkdir()
    (tmp_path / "orders" / "a.jpg").write_bytes(b"aaa")

    db_session.add_all(
        [
            StorageMigration(rel_path="orders/a.jpg", status=STATUS_PENDING, attempts=0),
            StorageMigration(
                rel_path="orders/b.jpg",
                status=STATUS_FAILED,
                attempts=1,
                error="迁移失败：网络超时",
            ),
        ]
    )
    await db_session.commit()

    response = await client.post(ADMIN_CLEANUP, headers=auth_header(admin_user))
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "2" in detail  # 两个未完成文件
    assert "迁移" in detail


async def test_cleanup_deletes_only_rows_verified_in_cos(
    client: AsyncClient, admin_user: dict, db_session, monkeypatch, tmp_path
):
    """全部 DONE 后允许清理；只有 COS 里验证存在的文件才会被删本地。"""
    from app.api.endpoints import admin_storage
    from app.core import config

    monkeypatch.setattr(config.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(admin_storage, "async_session_factory", _session_factory)

    (tmp_path / "orders").mkdir()
    existing = "orders/present.jpg"
    missing = "orders/gone-from-cos.jpg"
    (tmp_path / existing).write_bytes(b"present")
    (tmp_path / missing).write_bytes(b"missing")

    await _seed_setting(
        db_session,
        storage_mode="COS",
        cos_secret_id=encrypt_text(SECRET_ID),
        cos_secret_key=encrypt_text(SECRET_KEY),
        cos_bucket=BUCKET,
        cos_region=REGION,
    )
    db_session.add_all(
        [
            StorageMigration(
                rel_path=existing,
                status=STATUS_DONE,
                size_bytes=7,
                attempts=1,
            ),
            StorageMigration(
                rel_path=missing,
                status=STATUS_DONE,
                size_bytes=7,
                attempts=1,
            ),
        ]
    )
    await db_session.commit()
    storage_service.invalidate_storage_config_cache()

    exists_calls: list[str] = []
    delete_calls: list[str] = []

    async def _exists(key, _db):
        exists_calls.append(key)
        # 模拟 COS 侧对象已被外部删除：只允许删除仍存在的那个
        return key == existing

    async def _delete_local(key):
        # 替身按真实 delete_local 的行为删除本地文件（否则「已删除」断言无意义）
        delete_calls.append(key)
        (tmp_path / key).unlink(missing_ok=True)

    monkeypatch.setattr(storage_service, "exists", _exists)
    monkeypatch.setattr(storage_service, "delete_local", _delete_local)

    response = await client.post(ADMIN_CLEANUP, headers=auth_header(admin_user))
    assert response.status_code == 200
    assert response.json()["running"] is True

    # 后台任务在同一事件循环里跑，等它结束再断言
    if admin_storage.cleanup_job.task is not None:
        await asyncio.wait_for(asyncio.shield(admin_storage.cleanup_job.task), timeout=30)

    assert exists_calls.count(existing) >= 1
    assert delete_calls == [existing]
    # missing 的本地副本必须保留（COS 里已不存在）
    assert (tmp_path / missing).is_file()
    assert not (tmp_path / existing).is_file()

    status = await client.get(ADMIN_CLEANUP_STATUS, headers=auth_header(admin_user))
    body = status.json()
    assert body["running"] is False
    assert body["deleted"] == 1
    assert body["failed"] == 1
    assert body["remaining"] == 1


async def test_cleanup_is_refused_when_no_local_files_remain(
    client: AsyncClient, admin_user: dict, monkeypatch, tmp_path
):
    from app.core import config

    monkeypatch.setattr(config.settings, "UPLOAD_DIR", str(tmp_path))
    response = await client.post(ADMIN_CLEANUP, headers=auth_header(admin_user))
    assert response.status_code == 400
    assert "没有需要清理" in response.json()["detail"]


async def test_run_migration_marks_done_and_resumes_only_failed(
    db_session, monkeypatch, tmp_path
):
    """3 个文件：2 个成功 DONE，1 个强制失败；第二次运行只重试失败那个。

    台账行直接预置（绕过扫描入账），确保断点续传逻辑本身被验证：第一轮
    结束后只有 FAILED 行仍可被选中，``attempts`` 从 1 累加到 2。
    """
    from app.core import config

    monkeypatch.setattr(config.settings, "UPLOAD_DIR", str(tmp_path))
    (tmp_path / "orders").mkdir()
    rel_paths = ["orders/a.jpg", "orders/b.jpg", "orders/c.jpg"]
    for rel in rel_paths:
        (tmp_path / rel).write_bytes(b"x" * 10)
    failing = rel_paths[1]

    db_session.add_all(
        [
            StorageMigration(rel_path=rel, status=STATUS_PENDING, attempts=0)
            for rel in rel_paths
        ]
    )
    await db_session.commit()

    uploads: list[str] = []

    # 迁移任务按「显式 COS 配置」写对象，与当前 storage_mode 解耦；
    # 这里把配置固定成 COS，并替换 config 维度上的 save/exists 替身。
    async def _get_config(_db):
        return _COS_CONFIG_FOR_TESTS

    async def _save(data, key, content_type, _config):
        uploads.append(key)
        if key == failing:
            raise RuntimeError("模拟上传失败：网络中断")

    async def _exists(_key, _config):
        return True

    monkeypatch.setattr(storage_service, "get_storage_config", _get_config)
    monkeypatch.setattr(storage_service, "save_bytes_for_config", _save)
    monkeypatch.setattr(storage_service, "exists_for_config", _exists)

    await asyncio.wait_for(
        storage_migration.run_migration(_session_factory), timeout=30
    )

    rows = {
        row.rel_path: row
        for row in (await db_session.execute(select(StorageMigration))).scalars().all()
    }
    assert rows[rel_paths[0]].status == STATUS_DONE
    assert rows[rel_paths[2]].status == STATUS_DONE
    assert rows[failing].status == STATUS_FAILED
    assert rows[failing].attempts == 1
    assert "模拟上传失败" in (rows[failing].error or "")
    assert rows[rel_paths[0]].size_bytes == 10
    assert rows[rel_paths[0]].migrated_at is not None
    # 迁移阶段绝不删除本地文件
    assert all((tmp_path / rel).is_file() for rel in rel_paths)
    assert len(uploads) == 3

    # 第二轮：只有失败行被重试，成功行不会重复上传
    uploads.clear()
    await asyncio.wait_for(
        storage_migration.run_migration(_session_factory), timeout=30
    )

    # 后台任务用自己的会话提交。测试会话（expire_on_commit=False）会保留旧
    # 快照与 identity map，必须提交并 expire_all 后才能读到最新 attempts。
    await db_session.commit()
    db_session.expire_all()
    rows = {
        row.rel_path: row
        for row in (await db_session.execute(select(StorageMigration))).scalars().all()
    }
    assert uploads == [failing]
    # 两轮各尝试一次 -> 2
    assert rows[failing].attempts == 2
    assert rows[failing].status == STATUS_FAILED
    assert rows[rel_paths[0]].status == STATUS_DONE
    assert rows[rel_paths[0]].attempts == 1


async def test_run_migration_respects_pause_flag(
    db_session, monkeypatch, tmp_path
):
    """暂停标记在入账后立即生效：不再上传任何文件，剩余行保持 PENDING。"""
    from app.core import config

    monkeypatch.setattr(config.settings, "UPLOAD_DIR", str(tmp_path))
    (tmp_path / "orders").mkdir()
    (tmp_path / "orders" / "a.jpg").write_bytes(b"x" * 10)

    storage_migration.request_pause()

    calls: list = []

    async def _get_config(_db):
        return _COS_CONFIG_FOR_TESTS

    async def _save(*_args, **_kwargs):  # pragma: no cover - 不应被调用
        raise AssertionError("暂停后不应再上传")

    async def _exists(key, _config):
        calls.append(key)
        return True

    monkeypatch.setattr(storage_service, "get_storage_config", _get_config)
    monkeypatch.setattr(storage_service, "save_bytes_for_config", _save)
    monkeypatch.setattr(storage_service, "exists_for_config", _exists)

    await asyncio.wait_for(
        storage_migration.run_migration(_session_factory), timeout=30
    )

    assert storage_migration.migration_progress.paused is True
    rows = (await db_session.execute(select(StorageMigration))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == STATUS_PENDING
    assert calls == []
    storage_migration.clear_pause()


async def test_migration_still_writes_cos_even_if_mode_switched_to_local(
    db_session, monkeypatch, tmp_path
):
    """回归：迁移途中把 storage_mode 切回 LOCAL，仍必须往 COS 写。

    2026-11-23 真实踩到的 bug：旧 ``_migrate_one`` 用跟随当前模式的
    ``save_bytes`` / ``exists``。管理员在迁移中途切回 LOCAL 后，后续文件被
    "写"到本地（本来就在本地）、``exists`` 又查本地返回 True，于是被误记为
    DONE；清理阶段再去 COS 验证就全部 ``NoSuchResource``，台账与真实状态
    从此不一致。

    这里模拟：配置解析出来的 COS 配置固定不变（迁移开始时的快照），同时把
    **当前站点模式**成 LOCAL。断言迁移仍然调用的是 config-scoped 的 COS
    写入与校验，且从不触碰本地分支。
    """
    from app.core import config

    monkeypatch.setattr(config.settings, "UPLOAD_DIR", str(tmp_path))
    (tmp_path / "orders").mkdir()
    rel = "orders/a.jpg"
    (tmp_path / rel).write_bytes(b"x" * 10)

    db_session.add(StorageMigration(rel_path=rel, status=STATUS_PENDING, attempts=0))
    await db_session.commit()

    written: list[tuple[str, str]] = []

    async def _get_config(_db):
        return _COS_CONFIG_FOR_TESTS

    cos_objects: dict[str, bytes] = {}

    async def _exists_cos(key, cfg):
        assert cfg.mode == "COS"
        if cfg.mode != "COS":
            raise AssertionError("存在性校验必须针对 COS")
        # 只有真正被“上传”到 COS 的对象才算存在
        return key in cos_objects

    async def _save_cos_real(data, key, content_type, cfg):
        assert cfg.mode == "COS"
        written.append((key, cfg.mode))
        cos_objects[key] = data

    monkeypatch.setattr(storage_service, "get_storage_config", _get_config)
    monkeypatch.setattr(storage_service, "save_bytes_for_config", _save_cos_real)
    monkeypatch.setattr(storage_service, "exists_for_config", _exists_cos)
    # 旧签名必须绝对不被使用：一旦迁移走回跟随模式的路径就会写本地并误判 DONE
    monkeypatch.setattr(
        storage_service,
        "save_bytes",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("迁移不许用跟随模式的 save_bytes")),
    )
    monkeypatch.setattr(
        storage_service,
        "exists",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("迁移不许用跟随模式的 exists")),
    )

    await asyncio.wait_for(
        storage_migration.run_migration(_session_factory), timeout=30
    )

    rows = (await db_session.execute(select(StorageMigration))).scalars().all()
    assert rows[0].status == STATUS_DONE
    assert written == [(rel, "COS")]
    assert cos_objects == {rel: b"x" * 10}
    # 本地文件依旧保留（迁移阶段不删）
    assert (tmp_path / rel).is_file()


async def test_cleanup_marks_purged_so_rerun_migration_does_not_fail(
    db_session, monkeypatch, tmp_path
):
    """回归：清理删除本地副本后，台账必须变 PURGED。

    2026-11-23 实测：清理了 287 个文件后重置台账重跑迁移，这 287 行因本地
    源文件已被删除而全部 FAILED（"No such file or directory"）。根因是清理
    只删文件、不改状态，行仍停在 DONE，下一轮迁移仍把它们当待迁移目标。
    """
    from app.core import config
    from app.models.storage_migration import STATUS_PURGED

    monkeypatch.setattr(config.settings, "UPLOAD_DIR", str(tmp_path))
    (tmp_path / "orders").mkdir()
    rel = "orders/a.jpg"
    (tmp_path / rel).write_bytes(b"x" * 10)

    db_session.add(StorageMigration(rel_path=rel, status=STATUS_DONE, attempts=1))
    await db_session.commit()

    deleted: list[str] = []

    async def _exists(_key, _db):
        return True

    async def _delete_local(key):
        deleted.append(key)
        (tmp_path / key).unlink()

    monkeypatch.setattr(storage_service, "exists", _exists)
    monkeypatch.setattr(storage_service, "delete_local", _delete_local)

    await asyncio.wait_for(
        storage_migration.run_cleanup(_session_factory), timeout=30
    )

    await db_session.commit()
    db_session.expire_all()
    rows = (await db_session.execute(select(StorageMigration))).scalars().all()
    assert deleted == [rel]
    assert rows[0].status == STATUS_PURGED
    assert not (tmp_path / rel).exists()

    # 再跑一次迁移：PURGED 行不会被选中，因此不会再报"找不到源文件"
    async def _get_config(_db):
        return _COS_CONFIG_FOR_TESTS

    async def _never(*_a, **_k):  # pragma: no cover
        raise AssertionError("PURGED 行不应再被迁移")

    monkeypatch.setattr(storage_service, "get_storage_config", _get_config)
    monkeypatch.setattr(storage_service, "save_bytes_for_config", _never)

    await asyncio.wait_for(
        storage_migration.run_migration(_session_factory), timeout=30
    )

    await db_session.commit()
    db_session.expire_all()
    rows = (await db_session.execute(select(StorageMigration))).scalars().all()
    assert rows[0].status == STATUS_PURGED
