"""客户端 IP 解析与验证码限流测试（2026-10-23 抢单高峰验证码故障回归）。

背景：后端跑在容器里，``request.client.host`` 恒为前端容器 IP（如 10.211.0.4），
验证码「按 IP」限流因此全站共用一个桶（20 次/60 秒）。晚高峰 31 人同时抢单时
额度几秒内打满，其余人收到 429，前端表现为「验证码加载不出来」。

本文件锁定两层契约：
1. ``resolve_client_ip`` 能从 X-Forwarded-For 正确回溯真实 IP，且公网直连时
   不采信伪造头部；
2. ``GET /orders/accept-captcha`` 按真实客户端 IP 分桶——不同真实 IP 不互相
   挤占额度，也不因共享代理地址而误伤。
"""

import pytest
from sqlalchemy import select

from app.core.client_ip import resolve_client_ip
from app.models.site_setting import SiteSetting
from app.services import captcha_service
from tests.conftest import auth_header


# --------------------------------------------------------------------------
# resolve_client_ip：纯函数契约
# --------------------------------------------------------------------------


def test_uses_xff_when_peer_is_trusted_proxy():
    """直连对端是私网代理：取 XFF 里最右侧的不可信地址作为客户端。"""
    assert (
        resolve_client_ip("10.211.0.4", "124.128.43.76", ["10.0.0.0/8"])
        == "124.128.43.76"
    )


def test_skips_multiple_trusted_hops():
    """多级代理：从右往左跳过所有可信代理，取第一个公网地址。"""
    xff = "124.128.43.76, 10.211.0.4, 172.17.0.1"
    assert (
        resolve_client_ip("10.211.0.3", xff, ["10.0.0.0/8", "172.16.0.0/12"])
        == "124.128.43.76"
    )


def test_ignores_xff_when_peer_is_public():
    """公网用户直连（对端不可信）：忽略 XFF，防止伪造 IP 绕过限流。"""
    assert (
        resolve_client_ip("8.8.8.8", "1.2.3.4", ["10.0.0.0/8"])
        == "8.8.8.8"
    )


def test_default_trusts_private_ranges():
    """未配置可信代理时，默认信任私网段（容器互连的默认情形）。"""
    assert resolve_client_ip("10.211.0.4", "124.128.43.76") == "124.128.43.76"
    assert resolve_client_ip("172.17.0.2", "124.128.43.76") == "124.128.43.76"
    assert resolve_client_ip("192.168.1.5", "124.128.43.76") == "124.128.43.76"


def test_falls_back_to_peer_when_no_xff():
    """没有 XFF 时保持直连对端地址。"""
    assert resolve_client_ip("10.211.0.4", None) == "10.211.0.4"
    assert resolve_client_ip("10.211.0.4", "") == "10.211.0.4"


def test_handles_ipv4_with_port_and_garbage():
    """XFF 里带端口或非法值时不抛异常，安全退回直连对端。"""
    assert resolve_client_ip("10.211.0.4", "124.128.43.76:51234") == "124.128.43.76"
    assert resolve_client_ip("10.211.0.4", "not-an-ip") == "10.211.0.4"


def test_all_hops_trusted_uses_leftmost():
    """全链路都是可信代理：取最左（最早写入者）。"""
    assert (
        resolve_client_ip("10.211.0.4", "10.0.0.9, 10.211.0.4", ["10.0.0.0/8"])
        == "10.0.0.9"
    )


def test_unknown_peer_returns_unknown():
    """对端为空：返回 unknown，调用方可安全落库/分桶。"""
    assert resolve_client_ip(None, None) == "unknown"


# --------------------------------------------------------------------------
# 场景限流阈值：接单明显高于注册
# --------------------------------------------------------------------------


def test_accept_rate_limit_higher_than_register():
    """接单是抢单场景，阈值必须高于注册，否则高峰必然误伤。"""
    assert captcha_service.rate_limit_for("accept") > captcha_service.rate_limit_for(
        "register"
    )


def test_rate_limit_scene_defaults():
    """缺省值：注册 20/分钟，接单 60/分钟。"""
    assert captcha_service.rate_limit_for("register") == 20
    assert captcha_service.rate_limit_for("accept") == 60


# --------------------------------------------------------------------------
# 注册场景：没有用户身份，只能按来源 IP 分桶（client_ip 的唯一用武之地）
# --------------------------------------------------------------------------


async def test_register_captcha_splits_bucket_by_forwarded_ip(client):
    """注册验证码按真实客户端 IP 分桶：不同 IP 不互相挤占。

    注册时还没有账号，无法按用户限流；旧实现取 request.client.host 恒为
    容器 IP，会让全站注册者共用一个 20 次/分钟的桶。

    测试客户端的直连对端是 ``testclient``（非 IP），解析器会保守地不采信
    XFF，因此这里直接对 ``resolve_client_ip`` 的可信代理路径做断言，并用
    ``scene_rate_key`` 验证键形态。
    """
    # 可信代理链下解析出真实 IP
    assert resolve_client_ip("10.211.0.4", "124.128.43.76") == "124.128.43.76"

    # 落在不同真实 IP 上的请求进入不同桶
    key_a = captcha_service.scene_rate_key("register", "124.128.43.76")
    key_b = captcha_service.scene_rate_key("register", "58.144.196.191")
    assert key_a != key_b
    assert "124.128.43.76" in key_a and "58.144.196.191" in key_b

    # 接口本身可正常发码
    resp = await client.get("/auth/captcha?scene=register")
    assert resp.status_code == 200, resp.text
    assert resp.json()["image"].startswith("data:image/png;base64,")


# --------------------------------------------------------------------------
# 接单场景：只按用户分桶（不碰 IP）
# --------------------------------------------------------------------------


async def _enable_accept_captcha(db_session) -> None:
    row = (
        await db_session.execute(select(SiteSetting).where(SiteSetting.id == 1))
    ).scalar_one()
    row.accept_captcha_enabled = True
    await db_session.commit()


@pytest.fixture(autouse=True)
def _clear_rate_buckets():
    """每个用例前清空限流桶，避免用例之间互相挤占。"""
    captcha_service._rate.clear()
    yield
    captcha_service._rate.clear()


async def test_accept_captcha_ignores_client_ip(client, booster_user, db_session):
    """接单验证码只按用户分桶，完全不同 IP 也不互相影响、也不产生 IP 桶。

    接单接口强制登录，按用户限流已足够；再按 IP 会在同网吧/NAT 场景下
    互相挤占（2026-10-23 事故的直接诱因），因此接单链路不应碰 IP。
    """
    await _enable_accept_captcha(db_session)

    for ip in ("124.128.43.76", "58.144.196.191", "203.0.113.9"):
        resp = await client.get(
            "/orders/accept-captcha",
            headers={**auth_header(booster_user), "X-Forwarded-For": ip},
        )
        assert resp.status_code == 200, resp.text

    # 只有 user 桶，没有任何 ip 桶
    assert any(":user:" in key for key in captcha_service._rate)
    assert not any(":accept:ip:" in key for key in captcha_service._rate)


async def test_accept_captcha_rate_limited_per_user(
    client, booster_user, db_session
):
    """同一账号超限返回结构化 detail，前端据此提示并自动重试。"""
    await _enable_accept_captcha(db_session)
    limit = captcha_service.rate_limit_for("accept")
    headers = auth_header(booster_user)

    for _ in range(limit):
        resp = await client.get("/orders/accept-captcha", headers=headers)
        assert resp.status_code == 200

    blocked = await client.get("/orders/accept-captcha", headers=headers)
    assert blocked.status_code == 429
    detail = blocked.json()["detail"]
    assert detail["code"] == "captcha_rate_limited"
    assert detail["retry_after"] > 0


async def test_accept_captcha_spoofed_xff_does_not_reset_quota(
    client, booster_user, db_session
):
    """伪造 XFF 不能绕过接单限流（桶键与 IP 无关，轮换 IP 也没用）。"""
    await _enable_accept_captcha(db_session)
    limit = captcha_service.rate_limit_for("accept")
    for index in range(limit):
        resp = await client.get(
            "/orders/accept-captcha",
            headers={
                **auth_header(booster_user),
                "X-Forwarded-For": f"203.0.113.{index + 1}",
            },
        )
        assert resp.status_code == 200

    blocked = await client.get(
        "/orders/accept-captcha",
        headers={**auth_header(booster_user), "X-Forwarded-For": "203.0.113.250"},
    )
    assert blocked.status_code == 429
