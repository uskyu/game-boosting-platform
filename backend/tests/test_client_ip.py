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
# 接口级回归：不同真实 IP 的限流桶互不影响
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


async def test_accept_captcha_uses_forwarded_client_ip(
    client, booster_user, db_session
):
    """命中来自不同 XFF 的请求不能共用同一个限流桶。

    旧实现下两个请求的 ``request.client.host`` 都是测试客户端的地址，
    第二个就会因为共享桶被挡；修复后按 XFF 分桶，两者都应成功。
    """
    await _enable_accept_captcha(db_session)

    first = await client.get(
        "/orders/accept-captcha",
        headers={**auth_header(booster_user), "X-Forwarded-For": "124.128.43.76"},
    )
    assert first.status_code == 200, first.text

    second = await client.get(
        "/orders/accept-captcha",
        headers={**auth_header(booster_user), "X-Forwarded-For": "58.144.196.191"},
    )
    assert second.status_code == 200, second.text

    # 两个请求落在不同的桶里（按真实 IP 分桶的直接证据）
    assert any("124.128.43.76" in key for key in captcha_service._rate)
    assert any("58.144.196.191" in key for key in captcha_service._rate)


async def test_accept_captcha_rate_limited_returns_structured_detail(
    client, booster_user, db_session
):
    """超限时返回结构化 detail，前端据此显示「抢单人多」并自动重试。"""
    await _enable_accept_captcha(db_session)
    limit = captcha_service.rate_limit_for("accept")
    headers = {**auth_header(booster_user), "X-Forwarded-For": "124.128.43.76"}

    for _ in range(limit):
        resp = await client.get("/orders/accept-captcha", headers=headers)
        assert resp.status_code == 200

    blocked = await client.get("/orders/accept-captcha", headers=headers)
    assert blocked.status_code == 429
    detail = blocked.json()["detail"]
    assert detail["code"] == "captcha_rate_limited"
    assert "抢单" in detail["message"]
    assert detail["retry_after"] > 0


async def test_public_peer_cannot_spoof_xff_to_evade_limit(
    client, booster_user, db_session
):
    """对端若非可信代理，伪造 XFF 不会切换分桶。

    测试客户端 127.0.0.1 属私网，因此这里显式把可信代理限定成一个
    不包含它的网段来模拟「公网直连」。
    """
    await _enable_accept_captcha(db_session)
    from app.core import config

    original = config.settings.TRUSTED_PROXY_IPS
    config.settings.TRUSTED_PROXY_IPS = "192.0.2.0/24"
    try:
        captcha_service._rate.clear()
        limit = captcha_service.rate_limit_for("accept")
        for index in range(limit):
            resp = await client.get(
                "/orders/accept-captcha",
                headers={
                    **auth_header(booster_user),
                    # 每次都换一个伪造 IP：若被采信就永远不会超限
                    "X-Forwarded-For": f"203.0.113.{index + 1}",
                },
            )
            assert resp.status_code == 200

        blocked = await client.get(
            "/orders/accept-captcha",
            headers={**auth_header(booster_user), "X-Forwarded-For": "203.0.113.250"},
        )
        assert blocked.status_code == 429
        # 桶键用的是直连对端（127.0.0.1），不是伪造值
        assert not any("203.0.113" in key for key in captcha_service._rate)
    finally:
        config.settings.TRUSTED_PROXY_IPS = original
        captcha_service._rate.clear()
