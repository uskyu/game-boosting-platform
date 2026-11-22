"""预签名 URL / 签发时权限 / 台账复用 的测试（**不访问网络**）。

覆盖范围：
- ``next_expire_at`` 窗口对齐 + 同窗口稳定性
- ``key_from_url`` 的合法/非法输入（路径穿越、绝对 URL、空 key）
- ``sign_url``：COS 模式产出 URL、LOCAL 模式返回 None
- 台账复用：同窗口第二次返回**同一串** URL；剩余不足 1 小时时重新签发并
  覆盖同一行（浏览器缓存键因此不会抖动）
- ``authorize_url``：订单发布人 / 报名打手 / 管理员放行；无关用户、未登录
  拒绝；聊天参与者放行；提现二维码与售后凭证仅提交人本人与管理员
- ``POST /storage/resign``：本人在 COS 模式 200、无关用户 403、未登录 401
- LOCAL 模式下响应体保留原始 ``/uploads/...`` 且一次 SDK 都不调用

COS SDK 全部 monkeypatch，不发起任何网络请求。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.chat import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Message,
    MessageType,
)
from app.models.order import ClaimLifecycleStatus, Order, OrderClaim, OrderStatus
from app.models.order_after_sale import AfterSaleStatus, OrderAfterSale
from app.models.signed_url_cache import SignedUrlCache
from app.models.site_setting import SiteSetting
from app.models.user import User, UserRole
from app.models.user_session import UserSession
from app.models.withdrawal import WithdrawalChannel, WithdrawalRequest, WithdrawalStatus
from app.services import url_signing
from app.core.security import create_access_token

# 测试统一口令：这些账号只在测试库里存在，且不用于任何外部服务。
_TEST_PASSWORD_HASH = "not-a-real-hash"


# =============================================================================
# 替身：COS client / 存储配置
# =============================================================================


class _FakePresignClient:
    """假 COS client：只实现 ``get_presigned_url``，并记录每次调用。

    真实 SDK 每次签名都会写入当时的 ``q-sign-time``，所以**即使参数完全一致，
    返回的字符串也不同**。这里刻意复刻这个特性，才能证明「复用台账」真的
    在起作用，而不是 SDK 恰好稳定。
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get_presigned_url(self, Bucket, Key, Method, Expired=300):  # noqa: N803
        self.calls.append(
            {"Bucket": Bucket, "Key": Key, "Method": Method, "Expired": Expired}
        )
        return (
            f"https://{Bucket}.cos.example.com/{Key}"
            f"?q-ak=AKIDfake&q-sign-time={len(self.calls)}"
            f"&q-signature=sig{len(self.calls)}"
        )


class _FakeConfig:
    """``storage_service.StorageConfig`` 的等价替身（字段名与真实 dataclass 一致）。"""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.secret_id = "AKIDfake" if mode == "COS" else None
        self.secret_key = "fake-secret-key" if mode == "COS" else None
        self.bucket = "game-test-0001" if mode == "COS" else None
        self.region = "ap-chongqing" if mode == "COS" else None
        self.cdn_domain = None


@pytest.fixture
def fake_cos(monkeypatch) -> _FakePresignClient:
    """把 COS client 换成纯本地替身，并复位签名模块的进程内状态。"""
    client = _FakePresignClient()
    monkeypatch.setattr(url_signing, "_build_client", lambda config: client)
    monkeypatch.setattr(url_signing, "_CLIENT_CACHE", {})
    # 清理节流是进程内全局变量：不复位的话第一个用例跑完就不再清理了。
    monkeypatch.setattr(url_signing, "_last_cleanup_at", 0.0)
    return client


@pytest.fixture(autouse=True)
def patch_storage_config(monkeypatch):
    """让 ``url_signing._load_config`` 只读 DB 里的 mode，其余字段用固定替身。

    真实 ``storage_service.get_storage_config`` 要解密 Fernet 密文并做
    information_schema 列探测，在单元测试里既慢又依赖密钥；这里保留「读 DB
    行取 mode」的行为，字段名仍与生产一致。
    """

    async def _load_config(db):
        result = await db.execute(
            select(SiteSetting.storage_mode).where(SiteSetting.id == 1)
        )
        mode = result.scalar_one_or_none() or "LOCAL"
        return _FakeConfig("COS" if str(mode).upper() == "COS" else "LOCAL")

    monkeypatch.setattr(url_signing, "_load_config", _load_config)


async def _set_storage_mode(db, mode: str) -> None:
    """改站点存储模式并立即让 storage_service 的缓存失效。"""
    result = await db.execute(select(SiteSetting).where(SiteSetting.id == 1))
    setting = result.scalar_one_or_none()
    if setting is None:
        setting = SiteSetting(id=1, site_name="游戏服务平台")
        db.add(setting)
    setting.storage_mode = mode
    await db.commit()


@pytest.fixture
async def cos_mode(db_session) -> None:
    await _set_storage_mode(db_session, "COS")
    yield


@pytest.fixture
async def local_mode(db_session) -> None:
    await _set_storage_mode(db_session, "LOCAL")
    yield


# =============================================================================
# 纯函数：窗口与 key
# =============================================================================


def test_next_expire_at_is_future_and_window_aligned():
    now = 1_760_000_000
    expire_at = url_signing.next_expire_at(now)
    assert expire_at > now
    assert expire_at - now <= url_signing.SIGN_WINDOW_SECONDS
    assert expire_at % url_signing.SIGN_WINDOW_SECONDS == 0


def test_next_expire_at_is_stable_inside_the_same_window():
    """同一窗口内任意时刻必须给出同一个到期瞬间（台账复用的前提）。"""
    base = url_signing.next_expire_at(1_760_000_000)
    assert url_signing.next_expire_at(1_760_000_000 + 1) == base
    assert url_signing.next_expire_at(1_760_000_000 + 86_400) == base
    assert url_signing.next_expire_at(base) == base + url_signing.SIGN_WINDOW_SECONDS


@pytest.mark.parametrize(
    "url,expected",
    [
        ("/uploads/orders/x.jpg", "orders/x.jpg"),
        ("/uploads/chat/abc.png", "chat/abc.png"),
        ("/uploads/aftersales/7/a.webp", "aftersales/7/a.webp"),
        ("/uploads/booster-proof-3-abc.jpg", "booster-proof-3-abc.jpg"),
    ],
)
def test_key_from_url_accepts_valid_paths(url, expected):
    assert url_signing.key_from_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "/uploads/../etc/passwd",
        "/uploads/orders/../../x.jpg",
        "/uploads/orders/./x.jpg",
        "/uploads//x.jpg",
        "/uploads",
        "/uploads/",
        "",
        "https://evil.example/x.jpg",
        "//evil.example/x.jpg",
        "/uploads/orders/x.jpg?token=1",
        "/uploads/orders/x.jpg#frag",
        "/uploads/orders/x%2e%2e%2fsecrets",
        "uploads/orders/x.jpg",
        "orders/x.jpg",
        "/etc/passwd",
        "/uploads/C:/windows/x.jpg",
        "/uploads/orders\\x.jpg",
    ],
)
def test_key_from_url_rejects_traversal_and_absolute_urls(url):
    assert url_signing.key_from_url(url) is None
    assert url_signing.is_managed_url(url) is False


def test_is_managed_url_only_true_for_uploads_paths():
    assert url_signing.is_managed_url("/uploads/orders/x.jpg") is True
    assert url_signing.is_managed_url("https://cdn.example/x.jpg") is False
    assert url_signing.is_managed_url("/static/x.png") is False
    assert url_signing.is_managed_url(None) is False


# =============================================================================
# sign_url 与台账复用
# =============================================================================


async def test_sign_url_returns_url_in_cos_mode(db_session, cos_mode, fake_cos):
    url = await url_signing.sign_url(db_session, "orders/proof.jpg")
    assert url is not None
    assert url.startswith("https://game-test-0001.cos.example.com/orders/proof.jpg")
    # URL 里只有 AKID 与签名参数，绝不能出现 SecretKey
    assert "fake-secret-key" not in url
    assert "AKIDfake" in url
    # 与验收口径一致：GET + 7 天窗口内的 Expired
    assert fake_cos.calls[0]["Method"] == "GET"
    assert 0 < fake_cos.calls[0]["Expired"] <= url_signing.SIGN_WINDOW_SECONDS


async def test_sign_url_returns_none_in_local_mode(db_session, local_mode, fake_cos):
    assert await url_signing.sign_url(db_session, "orders/proof.jpg") is None
    # LOCAL 模式一次 SDK 都不能调用
    assert fake_cos.calls == []


async def test_sign_url_returns_none_for_invalid_key(db_session, cos_mode, fake_cos):
    assert await url_signing.sign_url(db_session, "../etc/passwd") is None
    assert fake_cos.calls == []


async def test_sign_url_reuses_cached_row_within_the_same_window(
    db_session, cos_mode, fake_cos
):
    """同窗口第二次必须拿到同一串 URL（浏览器缓存命中的唯一前提）。"""
    first = await url_signing.sign_url(db_session, "orders/stable.jpg")
    second = await url_signing.sign_url(db_session, "orders/stable.jpg")
    assert first and second
    assert first == second
    # 第二次台账命中，不应再触发 SDK
    assert len(fake_cos.calls) == 1

    rows = (
        await db_session.execute(
            select(SignedUrlCache).where(SignedUrlCache.object_key == "orders/stable.jpg")
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].url == first
    assert rows[0].expire_at == _expected_expire_dt()


def _expected_expire_dt() -> datetime:
    """当前窗口到期瞬间对应的 naive UTC datetime。"""
    return datetime.fromtimestamp(
        url_signing.next_expire_at(), tz=timezone.utc
    ).replace(tzinfo=None)


async def test_sign_url_regenerates_when_remaining_below_safety_and_upserts(
    db_session, cos_mode, fake_cos, monkeypatch
):
    """剩余有效期不足 1 小时 → 重新签发并覆盖同一 (key, expire_at) 行。"""
    first = await url_signing.sign_url(db_session, "orders/near-expiry.jpg")
    assert first

    # 把「现在」推到窗口末尾前 30 分钟：仍在同一个 7 天窗口内
    near_end = url_signing.next_expire_at() - 1800
    monkeypatch.setattr(url_signing, "time", _FrozenTime(near_end))

    second = await url_signing.sign_url(db_session, "orders/near-expiry.jpg")
    assert second and second != first
    assert len(fake_cos.calls) == 2

    rows = (
        await db_session.execute(
            select(SignedUrlCache).where(
                SignedUrlCache.object_key == "orders/near-expiry.jpg"
            )
        )
    ).scalars().all()
    # 唯一键仍是同一行：upsert 覆盖 url，而不是插入第二行
    assert len(rows) == 1
    assert rows[0].url == second


async def test_sign_url_generates_new_row_in_a_new_window(
    db_session, cos_mode, fake_cos, monkeypatch
):
    """跨窗口后必须重新签发：旧窗口的行不再命中。"""
    first = await url_signing.sign_url(db_session, "orders/next-window.jpg")
    assert first

    monkeypatch.setattr(url_signing, "time", _FrozenTime(url_signing.next_expire_at()))
    second = await url_signing.sign_url(db_session, "orders/next-window.jpg")
    assert second and second != first
    assert len(fake_cos.calls) == 2

    rows = (
        await db_session.execute(
            select(SignedUrlCache).where(
                SignedUrlCache.object_key == "orders/next-window.jpg"
            )
        )
    ).scalars().all()
    assert len(rows) == 2  # 新窗口一行；旧行由低频清理负责，不在本用例断言


class _FrozenTime:
    """极简 time 模块替身：``time.time()`` 被冻结，``monotonic()`` 保持可调用。"""

    def __init__(self, fixed):
        self._fixed = fixed

    def time(self):
        return self._fixed

    def monotonic(self):
        return float(self._fixed)


# =============================================================================
# sign_urls_in_payload
# =============================================================================


async def test_payload_signing_rewrites_managed_urls_only(db_session, cos_mode, fake_cos):
    from app.schemas.order import OrderResponse

    response = OrderResponse.model_validate(
        {
            "id": 1,
            # OrderResponse 必填字段：缺 user_id 会直接 422，本用例只关心 URL 改写
            "user_id": 1,
            "publisher_nickname": "测试发单人",
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "100.00",
            "status": OrderStatus.PENDING.value,
            "priority": 0,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "attachments": [
                {
                    "url": "/uploads/orders/pic.jpg",
                    "name": "pic.jpg",
                    "size": 10,
                    "content_type": "image/jpeg",
                }
            ],
        }
    )
    signed = await url_signing.sign_urls_in_payload(db_session, response)
    assert signed.attachments[0].url.startswith("https://")
    # 原对象不能被就地改写（避免污染 ORM/共享响应）
    assert response.attachments[0].url == "/uploads/orders/pic.jpg"


async def test_payload_signing_is_noop_in_local_mode(db_session, local_mode, fake_cos):
    payload = {"images": ["/uploads/aftersales/7/a.jpg"], "other": "https://cdn/x.jpg"}
    result = await url_signing.sign_urls_in_payload(db_session, payload)
    assert result["images"][0] == "/uploads/aftersales/7/a.jpg"
    assert result["other"] == "https://cdn/x.jpg"
    assert fake_cos.calls == []


async def test_payload_signing_handles_empty_and_none(db_session, cos_mode, fake_cos):
    assert await url_signing.sign_urls_in_payload(db_session, None) is None
    assert await url_signing.sign_urls_in_payload(db_session, []) == []
    assert await url_signing.sign_urls_in_payload(db_session, {"a": 1}) == {"a": 1}
    assert fake_cos.calls == []


# =============================================================================
# 权限
# =============================================================================


async def _make_user(db, *, username: str, role: UserRole = UserRole.USER) -> User:
    user = User(
        email=f"{username}@example.com",
        username=username,
        hashed_password=_TEST_PASSWORD_HASH,
        role=role,
        is_active=True,
        is_verified=True,
        can_publish=True,
        can_accept=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _make_order_with_attachment(db, *, publisher: User, url: str) -> Order:
    order = Order(
        user_id=publisher.id,
        game_name="王者荣耀",
        current_rank="钻石",
        target_rank="王者",
        price=Decimal("100.00"),
        status=OrderStatus.PENDING,
        priority=0,
        attachments=[
            {"url": url, "name": "a.jpg", "size": 10, "content_type": "image/jpeg"}
        ],
    )
    db.add(order)
    await db.flush()
    return order


async def _make_claim(db, *, order: Order, booster: User) -> OrderClaim:
    claim = OrderClaim(
        order_id=order.id,
        booster_id=booster.id,
        status=ClaimLifecycleStatus.CLAIMED,
    )
    db.add(claim)
    await db.flush()
    return claim


async def test_authorize_order_attachment_allows_publisher_claimant_admin(
    db_session, cos_mode
):
    publisher = await _make_user(db_session, username="pub1")
    claimant = await _make_user(db_session, username="boost1", role=UserRole.BOOSTER)
    stranger = await _make_user(db_session, username="stranger1")
    admin = await _make_user(db_session, username="admin1", role=UserRole.ADMIN)
    url = "/uploads/orders/authorize-1.jpg"
    order = await _make_order_with_attachment(db_session, publisher=publisher, url=url)
    await _make_claim(db_session, order=order, booster=claimant)
    await db_session.commit()

    assert await url_signing.authorize_url(db_session, publisher, url) is True
    assert await url_signing.authorize_url(db_session, claimant, url) is True
    assert await url_signing.authorize_url(db_session, admin, url) is True
    assert await url_signing.authorize_url(db_session, stranger, url) is False
    assert await url_signing.authorize_url(db_session, None, url) is False


async def test_authorize_rejects_unrelated_and_unknown_objects(db_session, cos_mode):
    publisher = await _make_user(db_session, username="pub2")
    stranger = await _make_user(db_session, username="stranger2")
    url = "/uploads/orders/authorize-2.jpg"
    await _make_order_with_attachment(db_session, publisher=publisher, url=url)
    await db_session.commit()

    assert await url_signing.authorize_url(db_session, stranger, url) is False
    # 不被任何业务表引用的对象：除管理员外无人能拿到签名
    assert (
        await url_signing.authorize_url(
            db_session, stranger, "/uploads/orders/never-referenced.jpg"
        )
        is False
    )
    # 非法输入永远拒绝
    assert await url_signing.authorize_url(db_session, publisher, "../etc/passwd") is False


async def test_authorize_delivery_attachment_for_claimant(db_session, cos_mode):
    """交付截图挂在 OrderClaim.delivery_attachments 上也必须认出来。"""
    publisher = await _make_user(db_session, username="pub3")
    claimant = await _make_user(db_session, username="boost3", role=UserRole.BOOSTER)
    stranger = await _make_user(db_session, username="stranger3")
    delivery_url = "/uploads/deliveries/authorize-3.jpg"
    order = await _make_order_with_attachment(
        db_session, publisher=publisher, url="/uploads/orders/other-3.jpg"
    )
    claim = await _make_claim(db_session, order=order, booster=claimant)
    claim.delivery_attachments = [
        {
            "url": delivery_url,
            "name": "d.jpg",
            "size": 10,
            "content_type": "image/jpeg",
        }
    ]
    await db_session.commit()

    assert await url_signing.authorize_url(db_session, claimant, delivery_url) is True
    assert await url_signing.authorize_url(db_session, publisher, delivery_url) is True
    assert await url_signing.authorize_url(db_session, stranger, delivery_url) is False


async def test_authorize_chat_image_allows_participants_only(db_session, cos_mode):
    publisher = await _make_user(db_session, username="pub4")
    booster = await _make_user(db_session, username="boost4", role=UserRole.BOOSTER)
    outsider = await _make_user(db_session, username="outsider4")
    admin = await _make_user(db_session, username="admin4", role=UserRole.ADMIN)
    url = "/uploads/chat/authorize-4.png"

    conversation = Conversation(
        type=ConversationType.ORDER,
        counterpart_user_id=booster.id,
    )
    db_session.add(conversation)
    await db_session.flush()
    for participant, role_snapshot in ((publisher, "USER"), (booster, "BOOSTER")):
        db_session.add(
            ConversationParticipant(
                conversation_id=conversation.id,
                user_id=participant.id,
                role_snapshot=role_snapshot,
            )
        )
    db_session.add(
        Message(
            conversation_id=conversation.id,
            sender_id=publisher.id,
            message_type=MessageType.IMAGE,
            content=url,
        )
    )
    await db_session.commit()

    assert await url_signing.authorize_url(db_session, publisher, url) is True
    assert await url_signing.authorize_url(db_session, booster, url) is True
    assert await url_signing.authorize_url(db_session, admin, url) is True
    assert await url_signing.authorize_url(db_session, outsider, url) is False
    assert await url_signing.authorize_url(db_session, None, url) is False


async def test_authorize_owner_scoped_images(db_session, cos_mode):
    """提现二维码 / 售后凭证 / 代练申请证明：仅提交人本人与管理员。"""
    owner = await _make_user(db_session, username="owner5")
    other = await _make_user(db_session, username="other5", role=UserRole.BOOSTER)
    admin = await _make_user(db_session, username="admin5", role=UserRole.ADMIN)

    qr_url = f"/uploads/withdrawals/{owner.id}/qr.png"
    after_sale_url = f"/uploads/aftersales/{owner.id}/proof.jpg"
    booster_proof_url = f"/uploads/booster-proof-{owner.id}-abc.jpg"

    db_session.add(
        WithdrawalRequest(
            user_id=owner.id,
            amount=Decimal("10.00"),
            fee_rate=Decimal("0.00"),
            fee_amount=Decimal("0.00"),
            net_amount=Decimal("10.00"),
            channel=WithdrawalChannel.ALIPAY,
            account_name="张三",
            account_no="zhangsan@example.com",
            qrcode_url=qr_url,
            status=WithdrawalStatus.PENDING,
        )
    )
    # 售后单只做归属判定用：order_id 指向本用例自己建的订单，避免外键悬空。
    order = await _make_order_with_attachment(
        db_session, publisher=owner, url="/uploads/orders/owner5.jpg"
    )
    db_session.add(
        OrderAfterSale(
            order_id=order.id,
            requester_id=owner.id,
            booster_id=other.id,
            reason="截图不合格，需要重新处理",
            images=[after_sale_url],
            amount=Decimal("10.00"),
            status=AfterSaleStatus.PENDING,
        )
    )
    owner.booster_application_proof_url = booster_proof_url
    await db_session.commit()

    for url in (qr_url, after_sale_url, booster_proof_url):
        assert await url_signing.authorize_url(db_session, owner, url) is True
        assert await url_signing.authorize_url(db_session, admin, url) is True
        assert await url_signing.authorize_url(db_session, other, url) is False
        assert await url_signing.authorize_url(db_session, None, url) is False


async def test_resign_for_user_is_permission_gated(db_session, cos_mode, fake_cos):
    publisher = await _make_user(db_session, username="pub6")
    stranger = await _make_user(db_session, username="stranger6")
    url = "/uploads/orders/resign-6.jpg"
    await _make_order_with_attachment(db_session, publisher=publisher, url=url)
    await db_session.commit()

    renewed = await url_signing.resign_for_user(db_session, publisher, url)
    assert renewed and renewed.startswith("https://")
    assert "fake-secret-key" not in renewed

    assert await url_signing.resign_for_user(db_session, stranger, url) is None
    assert await url_signing.resign_for_user(db_session, None, url) is None
    assert await url_signing.resign_for_user(db_session, publisher, "/etc/passwd") is None


async def test_resign_for_user_returns_raw_url_in_local_mode(db_session, local_mode):
    """LOCAL 模式本来就匿名可读：原样返回（历史行为不变）。"""
    publisher = await _make_user(db_session, username="pub7")
    url = "/uploads/orders/resign-7.jpg"
    await _make_order_with_attachment(db_session, publisher=publisher, url=url)
    await db_session.commit()
    assert await url_signing.resign_for_user(db_session, publisher, url) == url


# =============================================================================
# POST /storage/resign
# =============================================================================


def _token_for(user: User) -> str:
    """为测试账号签发 access token（设备会话由 ``_add_session`` 写入）。"""
    return create_access_token(
        subject=user.id,
        session_version=user.token_version,
        session_id="test-session-" + str(user.id),
    )


async def _add_session(db, user: User) -> None:
    """给用户注册一条有效设备会话，使其令牌能通过 ``get_current_user``。"""
    db.add(
        UserSession(
            user_id=user.id,
            sid="test-session-" + str(user.id),
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(hours=1),
        )
    )
    await db.commit()


async def test_resign_endpoint_200_for_owner(
    client: AsyncClient, db_session, cos_mode, fake_cos
):
    publisher = await _make_user(db_session, username="apiowner8")
    url = "/uploads/orders/resign-api-owner.jpg"
    await _make_order_with_attachment(db_session, publisher=publisher, url=url)
    await db_session.commit()
    await _add_session(db_session, publisher)

    response = await client.post(
        "/storage/resign",
        json={"url": url},
        headers={"Authorization": f"Bearer {_token_for(publisher)}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["url"].startswith("https://")
    assert "fake-secret-key" not in body["url"]


async def test_resign_endpoint_403_for_unrelated_user(
    client: AsyncClient, db_session, cos_mode, fake_cos
):
    publisher = await _make_user(db_session, username="apipub9")
    stranger = await _make_user(db_session, username="apistranger9")
    url = "/uploads/orders/resign-api-9.jpg"
    await _make_order_with_attachment(db_session, publisher=publisher, url=url)
    await db_session.commit()
    await _add_session(db_session, stranger)

    response = await client.post(
        "/storage/resign",
        json={"url": url},
        headers={"Authorization": f"Bearer {_token_for(stranger)}"},
    )
    assert response.status_code == 403
    # 文案不得暗示对象是否存在
    assert response.json()["detail"] == "无权访问该图片"


async def test_resign_endpoint_401_when_unauthenticated(
    client: AsyncClient, db_session, cos_mode
):
    response = await client.post(
        "/storage/resign", json={"url": "/uploads/orders/whatever.jpg"}
    )
    assert response.status_code == 401


async def test_resign_endpoint_rejects_non_managed_url(
    client: AsyncClient, db_session, cos_mode
):
    owner = await _make_user(db_session, username="apiowner10")
    await _add_session(db_session, owner)
    response = await client.post(
        "/storage/resign",
        json={"url": "https://evil.example/x.jpg"},
        headers={"Authorization": f"Bearer {_token_for(owner)}"},
    )
    assert response.status_code == 403
