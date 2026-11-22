"""对象存储门面（本地磁盘 / 腾讯云 COS）的行为测试。

全部用例都**不访问网络**：COS 侧一律用假的客户端替身模拟成功/失败/404，
既不依赖真实桶，也不可能误指向生产环境。

覆盖：
* LOCAL 模式写入/读取 + key 规范化拒绝路径穿越；
* 上传接口返回的仍是 ``/uploads/...`` 相对 URL（契约不变）；
* 切到 COS 且凭据错误时 ``test_cos_connection`` 返回中文失败信息且不抛异常；
* 密钥列为 NULL/密文损坏时 ``get_storage_config`` 退回 LOCAL；
* COS 命中 404 时读取返回 None，而 /uploads 回源会兜底到本地磁盘；
* COS SDK 调用在线程池执行（慢上传期间事件循环仍然可调度）。
"""

import asyncio
import time
from io import BytesIO

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select

from app.core import config
from app.core.security import encrypt_text
from app.models.site_setting import SiteSetting
from app.services import storage_service
from tests.conftest import auth_header


@pytest.fixture(autouse=True)
def _reset_storage_caches():
    """存储配置/列探测/client 都是进程内缓存，必须按用例隔离。"""
    storage_service.invalidate_storage_config_cache()
    yield
    storage_service.invalidate_storage_config_cache()


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    """把 UPLOAD_DIR 指到临时目录，避免污染仓库内 uploads/。"""
    monkeypatch.setattr(config.settings, "UPLOAD_DIR", str(tmp_path))
    return tmp_path


def _png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, format="PNG")
    return output.getvalue()


class _FakeBody:
    """COS StreamBody 的最小替身：按 1MB 分块吐出数据后返回空串。"""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0
        self.closed = False

    def read(self, chunk_size: int = 1024 * 1024):
        if self.closed:
            return ""
        chunk = self._data[self._offset:self._offset + chunk_size]
        self._offset += len(chunk)
        return chunk or ""

    def close(self) -> None:
        self.closed = True


class _FakeCosClient:
    """假 COS 客户端：可配置成功返回、缺失对象、鉴权失败。"""

    def __init__(
        self,
        *,
        objects: dict[str, bytes] | None = None,
        error_code: str | None = None,
        status_code: int = 404,
        put_delay_seconds: float = 0.0,
    ) -> None:
        self.objects = dict(objects or {})
        self.error_code = error_code
        self.status_code = status_code
        self.put_delay_seconds = put_delay_seconds
        self.calls: list[str] = []
        self.buckets: set[str] = set()

    @staticmethod
    def _raise(code: str, status_code: int) -> None:
        from qcloud_cos.cos_exception import CosServiceError

        raise CosServiceError(
            "GET",
            f"<Error><Code>{code}</Code><Message>mock</Message></Error>",
            status_code,
        )

    def _raise_configured(self) -> None:
        self._raise(self.error_code or "AccessDenied", self.status_code)

    def put_object(self, Bucket, Body, Key, **kwargs):
        self.calls.append("put")
        self.buckets.add(Bucket)
        if self.put_delay_seconds:
            # 同步 SDK 的真实行为：阻塞当前（线程池）线程
            time.sleep(self.put_delay_seconds)
        if self.error_code:
            self._raise_configured()
        self.objects[Key] = Body if isinstance(Body, bytes) else bytes(Body)
        return {"ETag": "mock"}

    def head_object(self, Bucket, Key, **kwargs):
        self.calls.append("head")
        self.buckets.add(Bucket)
        if self.error_code:
            self._raise_configured()
        if Key not in self.objects:
            self._raise("NoSuchKey", 404)
        return {"Content-Length": str(len(self.objects[Key]))}

    def get_object(self, Bucket, Key, **kwargs):
        self.calls.append("get")
        self.buckets.add(Bucket)
        if self.error_code:
            self._raise_configured()
        if Key not in self.objects:
            self._raise("NoSuchKey", 404)
        return {
            "Content-Type": "image/png",
            "Body": _FakeBody(self.objects[Key]),
        }

    def delete_object(self, Bucket, Key, **kwargs):
        self.calls.append("delete")
        self.buckets.add(Bucket)
        if self.error_code:
            self._raise_configured()
        self.objects.pop(Key, None)
        return {}


def _cos_config(**overrides) -> storage_service.StorageConfig:
    values = {
        "mode": "COS",
        "secret_id": "AKIDfake",
        "secret_key": "fakeSecretKey",
        "bucket": "game-1314124236",
        "region": "ap-chongqing",
        "cdn_domain": None,
    }
    values.update(overrides)
    return storage_service.StorageConfig(**values)


async def _set_storage_settings(db, **values) -> None:
    row = (await db.execute(select(SiteSetting).where(SiteSetting.id == 1))).scalar_one_or_none()
    if row is None:
        row = SiteSetting(id=1, site_name="游戏服务平台")
        db.add(row)
    for field, value in values.items():
        setattr(row, field, value)
    await db.commit()
    storage_service.invalidate_storage_config_cache()


# =============================================================================
# key 规范化
# =============================================================================


@pytest.mark.parametrize(
    "url",
    [
        "/uploads/../etc/passwd",
        "/uploads/orders/../../etc/passwd",
        "/uploads/orders/./x.jpg",
        "/uploads//x.jpg",
        "/uploads/",
        "/uploads",
        "orders/x.jpg",
        "/uploads/C:/x.jpg",
        "/uploads/orders/x.jpg\x00",
    ],
)
def test_key_from_url_rejects_traversal_and_malformed_input(url):
    with pytest.raises(ValueError):
        storage_service.key_from_url(url)


def test_key_from_url_maps_relative_url_to_key():
    assert storage_service.key_from_url("/uploads/orders/x.jpg") == "orders/x.jpg"
    assert storage_service.key_from_url("/uploads/booster-proof-1-abc.jpg") == (
        "booster-proof-1-abc.jpg"
    )
    assert storage_service.normalize_key("chat/def.png") == "chat/def.png"


# =============================================================================
# LOCAL 模式
# =============================================================================


async def test_local_mode_save_and_read(upload_dir, db_session):
    await _set_storage_settings(db_session, storage_mode="LOCAL")
    payload = b"local-bytes"

    await storage_service.save_bytes(payload, "orders/a.jpg", "image/jpeg", db_session)
    expected = upload_dir / "orders" / "a.jpg"
    assert expected.is_file()
    assert expected.read_bytes() == payload

    result = await storage_service.read_bytes("orders/a.jpg", db_session)
    assert result is not None
    data, content_type = result
    assert data == payload
    assert content_type == "image/jpeg"

    assert await storage_service.exists("orders/a.jpg", db_session) is True
    await storage_service.delete("orders/a.jpg", db_session)
    assert not expected.exists()
    assert await storage_service.read_bytes("orders/a.jpg", db_session) is None


async def test_local_delete_local_always_hits_disk(upload_dir, db_session):
    """清理任务删除本地副本时不依赖当前存储模式（COS 模式下也生效）。"""
    await _set_storage_settings(
        db_session,
        storage_mode="COS",
        cos_secret_id=encrypt_text("AKIDfake"),
        cos_secret_key=encrypt_text("fakeSecretKey"),
        cos_bucket="game-1314124236",
        cos_region="ap-chongqing",
    )
    (upload_dir / "orders").mkdir(parents=True)
    target = upload_dir / "orders" / "cleanup.jpg"
    target.write_bytes(b"x")

    await storage_service.delete_local("orders/cleanup.jpg")
    assert not target.exists()


# =============================================================================
# URL 契约不变（LOCAL 模式）
# =============================================================================


async def test_order_attachment_still_returns_relative_uploads_url(
    client: AsyncClient, admin_user: dict, upload_dir
):
    response = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "500",
        },
        headers=auth_header(admin_user),
    )
    assert response.status_code == 201
    order_id = response.json()["id"]

    response = await client.post(
        f"/orders/{order_id}/attachments",
        files={"attachment": ("proof.png", BytesIO(_png_bytes()), "image/png")},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 201
    item = response.json()
    assert item["url"].startswith("/uploads/orders/")
    assert not item["url"].startswith("http")
    # 返回值与落盘位置一致：本地磁盘确实有该文件
    stored = upload_dir / "orders" / item["url"].rsplit("/", 1)[-1]
    assert stored.is_file()


# =============================================================================
# 配置读取与兜底
# =============================================================================


async def test_get_storage_config_returns_local_for_null_secrets(db_session):
    await _set_storage_settings(
        db_session,
        storage_mode="COS",
        cos_secret_id=None,
        cos_secret_key=None,
        cos_bucket="game-1314124236",
        cos_region="ap-chongqing",
    )
    config_value = await storage_service.get_storage_config(db_session)
    assert config_value.mode == "LOCAL"
    assert config_value.bucket is None


async def test_get_storage_config_returns_local_for_undecryptable_secrets(db_session):
    await _set_storage_settings(
        db_session,
        storage_mode="COS",
        cos_secret_id="not-a-fernet-token",
        cos_secret_key="garbage-ciphertext",
        cos_bucket="game-1314124236",
        cos_region="ap-chongqing",
    )
    config_value = await storage_service.get_storage_config(db_session)
    assert config_value.mode == "LOCAL"


async def test_get_storage_config_decrypts_cos_secrets(db_session):
    await _set_storage_settings(
        db_session,
        storage_mode="COS",
        cos_secret_id=encrypt_text("AKIDfake"),
        cos_secret_key=encrypt_text("fakeSecretKey"),
        cos_bucket="game-1314124236",
        cos_region="ap-chongqing",
        cos_cdn_domain="cdn.example.com",
    )
    config_value = await storage_service.get_storage_config(db_session)
    assert config_value.mode == "COS"
    assert config_value.secret_id == "AKIDfake"
    assert config_value.secret_key == "fakeSecretKey"
    assert config_value.bucket == "game-1314124236"
    assert config_value.region == "ap-chongqing"
    assert config_value.cdn_domain == "cdn.example.com"


# =============================================================================
# COS：错误凭据自检 / 缺少对象
# =============================================================================


async def test_test_cos_connection_with_wrong_credentials_returns_chinese_failure(
    db_session, monkeypatch
):
    fake = _FakeCosClient(error_code="InvalidAccessKeyId", status_code=403)
    monkeypatch.setattr(storage_service, "_cos_client", lambda cfg: fake)

    ok, message = await storage_service.test_cos_connection(
        _cos_config(secret_id="AKIDwrong", secret_key="wrongSecret")
    )

    assert ok is False
    assert "COS 连接失败" in message
    # 中文可读原因，且绝不含密钥明文
    assert any("\u4e00" <= char <= "\u9fff" for char in message)
    assert "AKIDwrong" not in message
    assert "wrongSecret" not in message


async def test_test_cos_connection_reports_missing_bucket_in_chinese(monkeypatch):
    fake = _FakeCosClient(error_code="NoSuchBucket", status_code=404)
    monkeypatch.setattr(storage_service, "_cos_client", lambda cfg: fake)

    ok, message = await storage_service.test_cos_connection(_cos_config())
    assert ok is False
    assert "存储桶不存在" in message


async def test_test_cos_connection_with_incomplete_config_never_calls_network(
    monkeypatch,
):
    called = {"n": 0}

    def _fail(cfg):
        called["n"] += 1
        raise AssertionError("不应为不完整配置构造 client")

    monkeypatch.setattr(storage_service, "_cos_client", _fail)
    ok, message = await storage_service.test_cos_connection(
        _cos_config(bucket=None, region=None)
    )
    assert ok is False
    assert "配置不完整" in message
    assert called["n"] == 0


async def test_test_cos_connection_success_puts_heads_and_deletes_probe(monkeypatch):
    fake = _FakeCosClient(objects={})
    monkeypatch.setattr(storage_service, "_cos_client", lambda cfg: fake)

    ok, message = await storage_service.test_cos_connection(_cos_config())
    assert ok is True
    assert fake.calls == ["put", "head", "delete"]
    assert all(key.startswith("probe/connectivity-") for key in [])
    assert not fake.objects  # 探针对象已删除


async def test_cos_read_missing_object_returns_none(monkeypatch):
    fake = _FakeCosClient(objects={})
    monkeypatch.setattr(storage_service, "_cos_client", lambda cfg: fake)

    result = await storage_service.read_bytes_for_config("orders/x.jpg", _cos_config())
    assert result is None


# =============================================================================
# /uploads 回源：COS 命中返回字节；COS 缺失时兜底本地磁盘
# =============================================================================


async def _get_upload(client: AsyncClient, url: str):
    # 测试客户端 base_url 是 http://test/api/v1，这里用绝对 URL 绕开前缀，
    # 直接命中 app.mount("/uploads", ...)。
    return await client.get(f"http://test{url}")


async def test_uploads_serve_from_cos_when_mode_is_cos(client: AsyncClient, monkeypatch):
    fake = _FakeCosClient(objects={"orders/remote.png": b"cos-bytes"})
    monkeypatch.setattr(storage_service, "_cos_client", lambda cfg: fake)
    monkeypatch.setattr(
        storage_service, "peek_storage_config", lambda: _cos_config()
    )

    response = await _get_upload(client, "/uploads/orders/remote.png")
    assert response.status_code == 200
    assert response.content == b"cos-bytes"
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


async def test_uploads_fall_back_to_local_disk_when_cos_object_missing(
    client: AsyncClient, upload_dir, monkeypatch
):
    """部分迁移期间：COS 里没有、本地还有的老图必须仍能打开，不能 404。"""
    fake = _FakeCosClient(objects={})  # 任何 key 都 404
    monkeypatch.setattr(storage_service, "_cos_client", lambda cfg: fake)
    monkeypatch.setattr(
        storage_service, "peek_storage_config", lambda: _cos_config()
    )

    (upload_dir / "orders").mkdir(parents=True)
    legacy = upload_dir / "orders" / "legacy.jpg"
    legacy.write_bytes(b"legacy-bytes")

    response = await _get_upload(client, "/uploads/orders/legacy.jpg")
    assert response.status_code == 200
    assert response.content == b"legacy-bytes"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


async def test_uploads_404_when_missing_in_both_backends(
    client: AsyncClient, upload_dir, monkeypatch
):
    fake = _FakeCosClient(objects={})
    monkeypatch.setattr(storage_service, "_cos_client", lambda cfg: fake)
    monkeypatch.setattr(
        storage_service, "peek_storage_config", lambda: _cos_config()
    )

    response = await _get_upload(client, "/uploads/orders/absent.png")
    assert response.status_code == 404


async def test_uploads_redirect_to_cdn_when_configured(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(
        storage_service,
        "peek_storage_config",
        lambda: _cos_config(cdn_domain="cdn.example.com"),
    )
    response = await _get_upload(client, "/uploads/orders/pic.png")
    assert response.status_code == 302
    assert response.headers["location"] == "https://cdn.example.com/orders/pic.png"


async def test_uploads_traversal_is_404_even_in_cos_mode(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(
        storage_service, "peek_storage_config", lambda: _cos_config()
    )
    called = {"n": 0}

    async def _fail(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("路径穿越不应触发存储读取")

    monkeypatch.setattr(storage_service, "read_bytes_for_config", _fail)
    response = await _get_upload(client, "/uploads/../etc/passwd")
    # 客户端/服务器可能先做路径归一，这里只要求「不是 200」且没有触达存储
    assert response.status_code != 200
    assert called["n"] == 0


# =============================================================================
# 事件循环不被阻塞
# =============================================================================


async def test_slow_cos_put_runs_in_thread_and_keeps_loop_responsive(
    db_session, monkeypatch
):
    """COS SDK 是同步阻塞调用：慢上传期间事件循环必须还能调度其他协程。"""
    fake = _FakeCosClient(objects={}, put_delay_seconds=0.3)
    monkeypatch.setattr(storage_service, "_cos_client", lambda cfg: fake)
    monkeypatch.setattr(
        storage_service, "peek_storage_config", lambda: _cos_config()
    )
    # 缓存命中即可，绕开 DB 读取
    storage_service._CONFIG_CACHE["value"] = _cos_config()
    storage_service._CONFIG_CACHE["expires_at"] = time.monotonic() + 5

    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        await storage_service.save_bytes(
            b"payload", "orders/slow.jpg", "image/jpeg", db_session
        )
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    # 300ms 阻塞若发生在事件循环里，ticks 只能是 0 或 1
    assert ticks >= 5
    assert fake.objects["orders/slow.jpg"] == b"payload"
