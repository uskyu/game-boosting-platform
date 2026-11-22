"""通知提示音（site_settings.notification_sounds + /admin/sounds）测试。

覆盖：
- GET /admin/sounds 始终返回四个槽位的归一化配置（NULL 列 = 全部内置默认）；
- PUT 只改提交的槽位，其它槽位保持原值；
- 上传：MP3/WAV 通过；错误格式 / 超过 2MB / 空文件返回中文 400；
- reset：清空自定义 URL，但保留音量与静音状态；
- 公开 GET /site/settings 携带归一化后的 notification_sounds（前端据此播放）；
- 保存/上传/重置都登记 post-commit 广播 notification_sounds_changed（无数据）。
"""

import asyncio
from io import BytesIO

import pytest
from httpx import AsyncClient

from app.schemas.notification_sound import SOUND_SLOTS, default_sounds
from tests.conftest import auth_header

ADMIN_SOUNDS = "/admin/sounds"
PUBLIC_SETTINGS = "/site/settings"

# 一个最小的合法 MP3 文件头（ID3v2 标记 + 若干字节）；后端只校验类型/大小，
# 不解析音频内容，因此无需真实编码。
_MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 64


@pytest.fixture(scope="session", autouse=True)
def _mount_admin_sounds_router():
    """测试期挂载 /admin/sounds。

    生产接线由 app/api/router.py 负责（本模块顶部 ROUTER_INCLUDE_HINT 给出了
    需要加的那一行）。该文件由另一个改动负责，为了避免并行编辑冲突，测试里
    自行挂载；若生产接线已经生效则跳过，防止重复注册路由。
    """
    from app.api.endpoints.admin_sounds import router as admin_sounds_router
    from app.core.config import settings
    from app.main import app

    prefix = settings.API_V1_PREFIX
    already = any(
        getattr(route, "path", "").startswith(f"{prefix}/admin/sounds")
        for route in app.routes
    )
    if not already:
        app.include_router(admin_sounds_router, prefix=prefix)
    yield


@pytest.fixture
def audio_storage(tmp_path, monkeypatch):
    """把上传写入指向临时目录，并隔离存储层（另一 agent 的模块可能仍在改）。

    ``_persist_audio`` 优先调用 ``storage_service.save_bytes``；测试里用等价的
    本地写入替换它，保证 URL 契约与端点行为不受存储层进度影响。
    """
    from app.core import config

    monkeypatch.setattr(config.settings, "UPLOAD_DIR", str(tmp_path))
    try:
        from app.services import storage_service
    except ImportError:  # pragma: no cover - 存储层尚未落地时走本地兜底
        return tmp_path

    async def _save(data: bytes, key: str, content_type: str, db) -> None:
        target = tmp_path / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    monkeypatch.setattr(storage_service, "save_bytes", _save, raising=False)
    return tmp_path


def _assert_all_slots(body: dict) -> None:
    sounds = body["sounds"]
    assert set(sounds) == set(SOUND_SLOTS)
    for slot in SOUND_SLOTS:
        assert set(sounds[slot]) == {"url", "volume", "enabled"}
        assert sounds[slot]["url"] is None
        assert sounds[slot]["volume"] == 80
        assert sounds[slot]["enabled"] is True


# ---------------------------------------------------------------------------
# 读取 / 保存
# ---------------------------------------------------------------------------


async def test_get_returns_all_four_slots_normalized(
    client: AsyncClient, admin_user: dict
):
    """未配置过：GET 也返回四个槽位的完整默认（前端无需处理缺失槽位）。"""
    response = await client.get(ADMIN_SOUNDS, headers=auth_header(admin_user))
    assert response.status_code == 200, response.text
    _assert_all_slots(response.json())
    assert response.json()["sounds"] == default_sounds()


async def test_put_persists_subset_and_keeps_other_slots(
    client: AsyncClient, admin_user: dict
):
    """PUT 只覆盖提交的槽位：音量/静音/自定义 URL 落库，其它槽位不受影响。"""
    headers = auth_header(admin_user)
    payload = {
        "sounds": {
            "new_order": {"volume": 35, "enabled": False},
            "chat_message": {"url": "/uploads/sounds/chat-a1b2c3d4.mp3", "volume": 55},
        }
    }
    response = await client.put(ADMIN_SOUNDS, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    sounds = response.json()["sounds"]

    assert sounds["new_order"]["volume"] == 35
    assert sounds["new_order"]["enabled"] is False
    assert sounds["new_order"]["url"] is None
    # 未提交的自定义音频槽位保持默认
    assert sounds["claimed"] == {"url": None, "volume": 80, "enabled": True}
    assert sounds["cancel_requested"] == {"url": None, "volume": 80, "enabled": True}
    assert sounds["chat_message"]["url"] == "/uploads/sounds/chat-a1b2c3d4.mp3"
    assert sounds["chat_message"]["volume"] == 55
    assert sounds["chat_message"]["enabled"] is True

    # 第二次只改 claimed：之前的两处改动都还在（部分更新不重置其它槽位）
    response = await client.put(
        ADMIN_SOUNDS,
        json={"sounds": {"claimed": {"enabled": False}}},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    sounds = response.json()["sounds"]
    assert sounds["claimed"]["enabled"] is False
    assert sounds["new_order"]["volume"] == 35
    assert sounds["new_order"]["enabled"] is False
    assert sounds["chat_message"]["volume"] == 55

    # 再读一次确认已持久化（不是仅内存回显）
    response = await client.get(ADMIN_SOUNDS, headers=headers)
    assert response.json()["sounds"] == sounds


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/track.mp3",
        "/uploads/orders/track.mp3",
        "/static/track.mp3",
    ],
)
async def test_put_rejects_url_outside_sounds_directory(
    client: AsyncClient, admin_user: dict, url: str
):
    """自定义音频必须来自本站 /uploads/sounds/，防止后台被用来外链任意站点。"""
    response = await client.put(
        ADMIN_SOUNDS,
        json={"sounds": {"new_order": {"url": url}}},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 422, response.text


async def test_put_rejects_unknown_slot_and_out_of_range_volume(
    client: AsyncClient, admin_user: dict
):
    headers = auth_header(admin_user)
    response = await client.put(
        ADMIN_SOUNDS,
        json={"sounds": {"doorbell": {"volume": 50}}},
        headers=headers,
    )
    assert response.status_code == 422, response.text
    response = await client.put(
        ADMIN_SOUNDS,
        json={"sounds": {"new_order": {"volume": 101}}},
        headers=headers,
    )
    assert response.status_code == 422, response.text


async def test_put_slot_partial_payload_keeps_existing_url(
    client: AsyncClient, admin_user: dict, audio_storage
):
    """只调音量/静音时不能意外清掉已上传的自定义音频（仅显式 url=null 才清）。"""
    headers = auth_header(admin_user)
    upload = await client.post(
        ADMIN_SOUNDS + "/upload",
        data={"slot": "new_order"},
        files={"file": ("ding.mp3", BytesIO(_MP3), "audio/mpeg")},
        headers=headers,
    )
    assert upload.status_code == 200, upload.text
    custom_url = upload.json()["sounds"]["new_order"]["url"]

    response = await client.put(
        ADMIN_SOUNDS,
        json={"sounds": {"new_order": {"volume": 30}}},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    slot = response.json()["sounds"]["new_order"]
    assert slot["url"] == custom_url
    assert slot["volume"] == 30

    # 显式 url=null 表示恢复内置
    response = await client.put(
        ADMIN_SOUNDS,
        json={"sounds": {"new_order": {"url": None}}},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    slot = response.json()["sounds"]["new_order"]
    assert slot["url"] is None
    assert slot["volume"] == 30


async def test_admin_sounds_require_admin(client: AsyncClient, registered_user: dict):
    response = await client.get(ADMIN_SOUNDS, headers=auth_header(registered_user))
    assert response.status_code == 403, response.text


# ---------------------------------------------------------------------------
# 上传
# ---------------------------------------------------------------------------


async def test_upload_mp3_stores_under_sounds_and_activates_slot(
    client: AsyncClient, admin_user: dict, audio_storage
):
    headers = auth_header(admin_user)
    response = await client.post(
        ADMIN_SOUNDS + "/upload",
        data={"slot": "new_order"},
        files={"file": ("ding.mp3", BytesIO(_MP3), "audio/mpeg")},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    url = body["url"]
    assert url.startswith("/uploads/sounds/new_order-")
    assert url.endswith(".mp3")

    stored = audio_storage / "sounds" / url.rsplit("/", 1)[-1]
    assert stored.is_file()
    assert stored.read_bytes() == _MP3

    # 上传即生效：该槽位指向新文件，其它槽位仍是默认
    sounds = body["sounds"]
    assert sounds["new_order"]["url"] == url
    assert sounds["new_order"]["enabled"] is True
    assert sounds["claimed"]["url"] is None
    # 公开设置同步可见（前端重新拉取就能播放新音频）
    assert (
        await client.get(PUBLIC_SETTINGS)
    ).json()["notification_sounds"]["new_order"]["url"] == url


async def test_upload_keeps_existing_volume_and_enabled(
    client: AsyncClient, admin_user: dict, audio_storage
):
    """上传只替换音频文件，管理员调好的音量/静音状态不被重置。"""
    headers = auth_header(admin_user)
    await client.put(
        ADMIN_SOUNDS,
        json={"sounds": {"claimed": {"volume": 20, "enabled": False}}},
        headers=headers,
    )
    response = await client.post(
        ADMIN_SOUNDS + "/upload",
        data={"slot": "claimed"},
        files={"file": ("ok.wav", BytesIO(b"RIFF" + b"\x00" * 32), "audio/wav")},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    slot = response.json()["sounds"]["claimed"]
    assert slot["url"].startswith("/uploads/sounds/claimed-")
    assert slot["url"].endswith(".wav")
    assert slot["volume"] == 20
    assert slot["enabled"] is False


@pytest.mark.parametrize(
    ("filename", "content_type", "payload", "expected_detail"),
    [
        ("track.txt", "text/plain", b"hello", "仅支持 MP3 或 WAV 格式的音频文件"),
        # 2MB+1 的巨型 payload 用 sentinel 表示，避免 pytest 把 2MB 字节写进用例 ID
        ("track.mp3", "audio/mpeg", "OVERSIZE", "音频大小不能超过2MB"),
        ("empty.mp3", "audio/mpeg", b"", "上传音频不能为空"),
        ("track.mp3", "audio/wav", _MP3, "仅支持 MP3 或 WAV 格式的音频文件"),
    ],
)
async def test_upload_rejects_invalid_files_with_chinese_400(
    client: AsyncClient,
    admin_user: dict,
    audio_storage,
    filename: str,
    content_type: str,
    payload,
    expected_detail: str,
):
    if payload == "OVERSIZE":
        payload = b"x" * (2 * 1024 * 1024 + 1)
    response = await client.post(
        ADMIN_SOUNDS + "/upload",
        data={"slot": "cancel_requested"},
        files={"file": (filename, BytesIO(payload), content_type)},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == expected_detail


async def test_upload_rejects_unknown_slot(
    client: AsyncClient, admin_user: dict, audio_storage
):
    response = await client.post(
        ADMIN_SOUNDS + "/upload",
        data={"slot": "doorbell"},
        files={"file": ("ding.mp3", BytesIO(_MP3), "audio/mpeg")},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "未知的通知音频槽位：doorbell"


# ---------------------------------------------------------------------------
# 恢复默认
# ---------------------------------------------------------------------------


async def test_reset_clears_url_but_keeps_volume_and_enabled(
    client: AsyncClient, admin_user: dict, audio_storage
):
    headers = auth_header(admin_user)
    upload = await client.post(
        ADMIN_SOUNDS + "/upload",
        data={"slot": "chat_message"},
        files={"file": ("blip.mp3", BytesIO(_MP3), "audio/mpeg")},
        headers=headers,
    )
    assert upload.status_code == 200, upload.text
    custom_url = upload.json()["sounds"]["chat_message"]["url"]
    assert custom_url

    await client.put(
        ADMIN_SOUNDS,
        json={"sounds": {"chat_message": {"volume": 45}}},
        headers=headers,
    )

    response = await client.post(
        ADMIN_SOUNDS + "/reset",
        json={"slot": "chat_message"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    slot = response.json()["sounds"]["chat_message"]
    assert slot["url"] is None
    assert slot["volume"] == 45
    assert slot["enabled"] is True
    # 其它槽位不变
    assert response.json()["sounds"]["new_order"] == {
        "url": None,
        "volume": 80,
        "enabled": True,
    }


async def test_reset_rejects_unknown_slot(client: AsyncClient, admin_user: dict):
    response = await client.post(
        ADMIN_SOUNDS + "/reset",
        json={"slot": "doorbell"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# 公开站点设置
# ---------------------------------------------------------------------------


async def test_public_settings_include_normalized_sounds(
    client: AsyncClient, admin_user: dict
):
    """公开设置默认携带四个槽位；后台改动后公开接口同步反映。"""
    body = (await client.get(PUBLIC_SETTINGS)).json()
    assert body["notification_sounds"] == default_sounds()

    await client.put(
        ADMIN_SOUNDS,
        json={
            "sounds": {
                "new_order": {"url": "/uploads/sounds/new-order-abcd1234.mp3", "volume": 60},
                "chat_message": {"enabled": False},
            }
        },
        headers=auth_header(admin_user),
    )
    body = (await client.get(PUBLIC_SETTINGS)).json()
    sounds = body["notification_sounds"]
    assert set(sounds) == set(SOUND_SLOTS)
    assert sounds["new_order"]["url"] == "/uploads/sounds/new-order-abcd1234.mp3"
    assert sounds["new_order"]["volume"] == 60
    assert sounds["new_order"]["enabled"] is True
    assert sounds["chat_message"]["enabled"] is False
    assert sounds["claimed"] == {"url": None, "volume": 80, "enabled": True}


async def test_site_settings_update_does_not_touch_sounds(
    client: AsyncClient, admin_user: dict
):
    """站点设置保存接口不管理提示音：保存其它设置不会清空已有音频配置。"""
    headers = auth_header(admin_user)
    await client.put(
        ADMIN_SOUNDS,
        json={"sounds": {"claimed": {"volume": 10, "enabled": False}}},
        headers=headers,
    )
    response = await client.put(
        "/admin/site/settings",
        json={"site_name": "改名后的平台"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    sounds = response.json()["notification_sounds"]
    assert sounds["claimed"]["volume"] == 10
    assert sounds["claimed"]["enabled"] is False


# ---------------------------------------------------------------------------
# post-commit 广播
# ---------------------------------------------------------------------------


class _RecordingManager:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def broadcast(self, payload: dict) -> None:
        self.payloads.append(payload)


async def test_sounds_changed_broadcast_runs_after_commit(monkeypatch):
    """事件登记后不会在事务提交前发送；commit 后发出无数据的变更通知。"""
    import app.api.endpoints.admin_sounds as admin_sounds_module
    from app.core.post_commit import drain_registry, start_registry, stop_registry

    manager = _RecordingManager()
    monkeypatch.setattr(admin_sounds_module, "get_connection_manager", lambda: manager)

    token = start_registry()
    try:
        await admin_sounds_module._broadcast_sounds_changed()
        assert manager.payloads == []
        await drain_registry()
        await asyncio.sleep(0)
        assert manager.payloads == [
            {"event": "notification_sounds_changed", "data": {}}
        ]
    finally:
        stop_registry(token)


async def test_admin_put_broadcasts_after_commit(
    client: AsyncClient, admin_user: dict, monkeypatch
):
    """真实请求路径：PUT 保存成功后（commit 之后）才广播，且只发一次。"""
    import app.api.endpoints.admin_sounds as admin_sounds_module

    manager = _RecordingManager()
    monkeypatch.setattr(admin_sounds_module, "get_connection_manager", lambda: manager)

    response = await client.put(
        ADMIN_SOUNDS,
        json={"sounds": {"cancel_requested": {"volume": 70}}},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200, response.text
    # 响应返回后 post-commit 钩子以后台任务执行，让出一次事件循环即可断言
    await asyncio.sleep(0)
    assert manager.payloads == [
        {"event": "notification_sounds_changed", "data": {}}
    ]
