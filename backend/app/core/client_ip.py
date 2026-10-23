"""客户端 IP 解析：从可信代理链回溯真实来源 IP。

背景（2026-10-23 生产事故）
--------------------------
部署拓扑是：用户 → 宿主 nginx（BT 面板）→ 前端容器 nginx → 后端 uvicorn。
uvicorn 默认 ``forwarded_allow_ips="127.0.0.1"``，而实际连入后端的是前端容器
的 docker 内网 IP（如 ``10.211.0.4``），不在信任名单里，因此 uvicorn 会**整体
忽略** ``X-Forwarded-For``，``request.client.host`` 恒为容器 IP。

后果：``/orders/accept-captcha`` 的「按 IP」限流桶全站共用一个（20 次/60 秒），
晚高峰 31 个打手同时抢单时，额度在最初几秒被少数人打满，其余人拿到 429，
前端表现就是「验证码加载不出来」。24 小时内 166 次 429 全部命中该接口。

本模块的作用
------------
不依赖 uvicorn 的代理头开关，直接在应用层从 ``X-Forwarded-For`` 右往左跳可信
代理，取第一个不可信的地址。这样：

- 修复不依赖部署参数，容器 IP 变化也不会失效；
- 默认信任 RFC1918 私网段（容器/宿主机互连必然是私网），公网客户端无法伪造；
- 显式配置 ``TRUSTED_PROXY_IPS`` 时可只信任指定地址。

安全边界：只有当**直连对端**是可信代理时才采信 XFF。公网用户直连（对端是公网
IP）时忽略 XFF，避免被 ``X-Forwarded-For: 1.2.3.4`` 绕过限流。
"""

from __future__ import annotations

import ipaddress
from typing import Iterable

_PRIVATE_NETWORKS: tuple[ipaddress._BaseNetwork, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
)


def _parse_ip(value: str | None) -> ipaddress._BaseAddress | None:
    """解析单个 IP；带端口（``1.2.3.4:5678``）或非法值返回 None。"""
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    # 去掉可能的 IPv6 方括号与 IPv4:port 后缀
    if text.startswith("["):
        end = text.find("]")
        if end != -1:
            text = text[1:end]
    elif text.count(":") == 1 and "." in text:
        text = text.split(":", 1)[0]
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def _build_trusted(configured: Iterable[str] | None) -> tuple[ipaddress._BaseNetwork, ...]:
    """把配置的 CIDR/IP 列表转成网络对象；未配置时用私网段默认值。"""
    if not configured:
        return _PRIVATE_NETWORKS
    networks: list[ipaddress._BaseNetwork] = []
    for raw in configured:
        item = str(raw).strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            # 配置写错不该让整个站点起不来：跳过并在解析时退化为「不可信」
            continue
    return tuple(networks) if networks else _PRIVATE_NETWORKS


def _is_trusted(ip: ipaddress._BaseAddress, trusted: tuple[ipaddress._BaseNetwork, ...]) -> bool:
    return any(ip in network for network in trusted)


def resolve_client_ip(
    peer_host: str | None,
    forwarded_for: str | None,
    trusted_proxies: Iterable[str] | None = None,
) -> str:
    """解析真实客户端 IP。

    Args:
        peer_host: 直连对端地址（``request.client.host``），可能是容器/代理地址。
        forwarded_for: ``X-Forwarded-For`` 原始头部，形如 ``client, proxy1, proxy2``。
        trusted_proxies: 可信代理的 CIDR/IP 列表；``None`` 表示信任全部私网段。

    Returns:
        真实客户端 IP 字符串。无法解析时返回 ``"unknown"``；未采信 XFF 时返回
        直连对端地址。
    """
    trusted = _build_trusted(trusted_proxies)
    peer = _parse_ip(peer_host)

    # 直连方不是可信代理（例如公网用户绕过 nginx 直连）：忽略 XFF 防伪造
    if peer is None or not _is_trusted(peer, trusted):
        return peer_host or "unknown"

    if not forwarded_for:
        return peer_host or "unknown"

    hops = [item.strip() for item in forwarded_for.split(",") if item.strip()]
    if not hops:
        return peer_host or "unknown"

    # 从右往左（最靠近本站的代理在右）：跳过可信代理，第一个不可信的即客户端
    for hop in reversed(hops):
        candidate = _parse_ip(hop)
        if candidate is None:
            # 头部里出现非法值：无法安全判定，退回直连对端
            return peer_host or "unknown"
        if not _is_trusted(candidate, trusted):
            return str(candidate)
    # 全链路都是可信代理：取最左（最早写入者）作为客户端
    leftmost = _parse_ip(hops[0])
    return str(leftmost) if leftmost is not None else (peer_host or "unknown")


def client_ip_from_request(request) -> str:
    """FastAPI ``Request`` 版本：按配置的可信代理解析真实客户端 IP。

    供需要「按来源 IP 分桶」「记录登录 IP」的端点统一调用，避免各处直接读
    ``request.client.host``（在反向代理后面恒为代理地址）。
    """
    from app.core.config import settings

    client = getattr(request, "client", None)
    peer_host = getattr(client, "host", None) if client is not None else None
    headers = getattr(request, "headers", None)
    forwarded_for = headers.get("x-forwarded-for") if headers is not None else None
    return resolve_client_ip(peer_host, forwarded_for, settings.trusted_proxy_ips)

