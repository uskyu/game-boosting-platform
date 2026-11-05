"""充值订单主动查询 / 补单的纯单元测试（无数据库、无真实网络）。

上游查询的真实响应已实测（见模块 docstring）：HTTP 永远是 200，
是否查到订单只看 JSON 里的 ``code``，是否已支付只看 ``status``。

所有请求都走 ``httpx.MockTransport``：既能精确构造上游响应，也能顺带断言
发出去的查询参数（act / pid / out_trade_no），而且不会真的访问外网。
数据库相关路径用轻量的伪 session 顶替，避免触发 conftest 的全库重建。
"""

from decimal import Decimal

import httpx

from app.models.payment_gateway import PaymentGateway
from app.models.recharge import RechargeOrder, RechargeStatus
from app.services import recharge_query_service as service

GATEWAY_URL = "https://pay.example.com/xpay/epay"

# 在 monkeypatch 之前抓住真实类：工厂里若再取 ``httpx.AsyncClient`` 会拿到
# 被替换后的自己，直接无限递归。
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _order(**overrides) -> RechargeOrder:
    """一条待支付充值单（只在内存里，不写库）。"""
    data = {
        "trade_no": "RCG1NOABCDEF1700000000",
        "user_id": 1,
        "amount": Decimal("1000.00"),
        "payment_method": "alipay",
        "status": RechargeStatus.PENDING,
        "gateway_id": 7,
    }
    data.update(overrides)
    return RechargeOrder(**data)


def _gateway(**overrides) -> PaymentGateway:
    """易支付标准布局的网关：未填 query_url 时自动推导 ``{基地址}/api.php``。"""
    data = {
        "id": 7,
        "name": "马支付",
        "submit_url": GATEWAY_URL,
        "append_submit_php": True,
        "merchant_id": "12345",
        "secret_key": "test-secret-key",
        "query_url": None,
        "enabled": True,
        "is_active": True,
        "alipay_enabled": True,
        "wxpay_enabled": True,
    }
    data.update(overrides)
    return PaymentGateway(**data)


def _patch_client(monkeypatch, handler) -> None:
    """把 ``httpx.AsyncClient`` 换成挂了 MockTransport 的同款客户端。"""

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        # 不传 base_url：被测代码用的是完整查询地址
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    monkeypatch.setattr(service, "_RETRY_BACKOFF_SECONDS", 0.0)


def _ok_payload(**overrides) -> dict:
    """实测的「查到了」响应：注意 money 是元字符串，pid 是 int。"""
    payload = {
        "code": 1,
        "msg": "查询订单号成功！",
        "trade_no": "20261105203743294947",
        "out_trade_no": "RCG1NOABCDEF1700000000",
        "type": "alipay",
        "pid": 12345,
        "addtime": "2026-11-05 20:37:43",
        "endtime": "2026-11-05 20:38:10",
        "name": "账户充值 1000.00元",
        "money": "1000.00",
        "param": "",
        "buyer": "",
        "status": 1,
    }
    payload.update(overrides)
    return payload


def _recorder(payload: dict, *, status_code: int = 200):
    """返回 (handler, calls)：记录每次请求，始终回同一份 JSON。"""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status_code, json=payload)

    return handler, calls


async def test_paid_status_returns_paid_and_calls_standard_query_endpoint(monkeypatch):
    """status=1 → PAID；同时确认查询参数符合上游协议。"""
    handler, calls = _recorder(_ok_payload())
    _patch_client(monkeypatch, handler)

    result = await service.query_gateway_order(
        _gateway(), "RCG1NOABCDEF1700000000", expected_amount=Decimal("1000.0")
    )

    assert result.outcome is service.QueryOutcome.PAID
    assert len(calls) == 1
    request = calls[0]
    assert request.method == "GET"
    assert request.url.path == "/xpay/epay/api.php"
    assert request.url.params["act"] == "order"
    assert request.url.params["pid"] == "12345"
    assert request.url.params["out_trade_no"] == "RCG1NOABCDEF1700000000"
    # 上游原始 JSON 保留下来供对账（money 仍是元字符串）
    assert result.upstream["trade_no"] == "20261105203743294947"
    assert result.upstream["money"] == "1000.00"


async def test_numeric_money_is_compared_as_decimal(monkeypatch):
    """上游 money 是数字时也能正确比对：1000.0 / 1000 都等于 1000.00。"""
    for money in (1000.0, 1000, "1000.0"):
        handler, _ = _recorder(_ok_payload(money=money))
        _patch_client(monkeypatch, handler)

        result = await service.query_gateway_order(
            _gateway(), "RCG1NOABCDEF1700000000", expected_amount=Decimal("1000.00")
        )

        assert result.outcome is service.QueryOutcome.PAID, money


async def test_status_zero_is_unpaid_and_money_is_not_even_needed(monkeypatch):
    """status=0 → UNPAID（订单保持 PENDING，不改状态）。"""
    handler, _ = _recorder(_ok_payload(status=0, money="0.00"))
    _patch_client(monkeypatch, handler)

    result = await service.query_gateway_order(
        _gateway(), "RCG1NOABCDEF1700000000", expected_amount=Decimal("1000.00")
    )

    assert result.outcome is service.QueryOutcome.UNPAID
    assert "未支付" in result.message


async def test_code_zero_is_not_found_even_with_http_200(monkeypatch):
    """HTTP 200 + code=0 → NOT_FOUND（必须先看 code 再看 body）。"""
    handler, _ = _recorder({"code": 0, "msg": "没有找到订单"})
    _patch_client(monkeypatch, handler)

    result = await service.query_gateway_order(_gateway(), "RCG1NOABCDEF1700000000")

    assert result.outcome is service.QueryOutcome.NOT_FOUND
    assert "没有找到订单" in result.message


async def test_amount_mismatch_is_rejected(monkeypatch):
    """上游只付了 0.01 元：绝不能按订单金额入账。"""
    handler, _ = _recorder(_ok_payload(money="0.01"))
    _patch_client(monkeypatch, handler)

    result = await service.query_gateway_order(
        _gateway(), "RCG1NOABCDEF1700000000", expected_amount=Decimal("1000.00")
    )

    assert result.outcome is service.QueryOutcome.AMOUNT_MISMATCH
    assert "不一致" in result.message


async def test_unparsable_money_is_treated_as_mismatch(monkeypatch):
    """金额无法解析时同样不确认到账（宁可人工核对，不可凭猜入账）。"""
    handler, _ = _recorder(_ok_payload(money="not-a-number"))
    _patch_client(monkeypatch, handler)

    result = await service.query_gateway_order(
        _gateway(), "RCG1NOABCDEF1700000000", expected_amount=Decimal("1000.00")
    )

    assert result.outcome is service.QueryOutcome.AMOUNT_MISMATCH


async def test_non_json_body_is_error(monkeypatch):
    """返回 HTML（网关错误页）→ ERROR，而不是 NOT_FOUND。"""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text="<html>gateway down</html>")

    _patch_client(monkeypatch, handler)

    result = await service.query_gateway_order(_gateway(), "RCG1NOABCDEF1700000000")

    assert result.outcome is service.QueryOutcome.ERROR
    assert "JSON" in result.message
    # 非 JSON 是上游明确答复，不重试
    assert len(calls) == 1


async def test_http_500_is_error_and_not_retried(monkeypatch):
    """HTTP 500 → ERROR；这是确定性答复，不浪费重试次数。"""
    handler, calls = _recorder({"code": 1}, status_code=500)
    _patch_client(monkeypatch, handler)

    result = await service.query_gateway_order(_gateway(), "RCG1NOABCDEF1700000000")

    assert result.outcome is service.QueryOutcome.ERROR
    assert "500" in result.message
    assert len(calls) == 1


async def test_gateway_without_query_url_is_unsupported(monkeypatch):
    """不补 submit.php 且没填 query_url：不臆造路径，直接 UNSUPPORTED。"""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_ok_payload())

    _patch_client(monkeypatch, handler)
    gateway = _gateway(append_submit_php=False, query_url=None)

    result = await service.query_gateway_order(gateway, "RCG1NOABCDEF1700000000")

    assert result.outcome is service.QueryOutcome.UNSUPPORTED
    assert "未配置查询地址" in result.message
    assert calls == []


async def test_network_error_is_retried_then_reported(monkeypatch):
    """连接失败可以重试，但最多 MAX_QUERY_RETRIES 次，最终仍失败就是 ERROR。"""
    attempts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        raise httpx.ConnectError("connection refused", request=request)

    _patch_client(monkeypatch, handler)

    result = await service.query_gateway_order(_gateway(), "RCG1NOABCDEF1700000000")

    assert result.outcome is service.QueryOutcome.ERROR
    assert len(attempts) == service.MAX_QUERY_RETRIES + 1


async def test_network_error_recovers_on_retry(monkeypatch):
    """第一次连接失败、第二次成功：应返回真实结论而不是 ERROR。"""
    attempts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.ConnectError("connection reset", request=request)
        return httpx.Response(200, json=_ok_payload())

    _patch_client(monkeypatch, handler)

    result = await service.query_gateway_order(
        _gateway(), "RCG1NOABCDEF1700000000", expected_amount=Decimal("1000.00")
    )

    assert result.outcome is service.QueryOutcome.PAID
    assert len(attempts) == 2


# ---------------------------------------------------------------------------
# sync_recharge_order：钱只在上游「已付 + 金额一致」时才动
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, value) -> None:
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """只回答「按 id 取网关」这一条查询，不碰真实数据库。"""

    def __init__(self, gateway: PaymentGateway | None) -> None:
        self.gateway = gateway
        self.statements: list = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _FakeResult(self.gateway)


class _StubWalletService:
    def __init__(self, order: RechargeOrder) -> None:
        self.order = order
        self.calls: list[dict] = []

    async def complete_recharge(
        self, trade_no: str, *, epay_trade_no=None, notify_payload=None
    ):
        self.calls.append(
            {
                "trade_no": trade_no,
                "epay_trade_no": epay_trade_no,
                "notify_payload": notify_payload,
            }
        )
        self.order.epay_trade_no = epay_trade_no
        self.order.status = RechargeStatus.SUCCESS
        return self.order


async def test_sync_paid_order_is_credited_with_upstream_payload(monkeypatch):
    """已付 + 金额一致 → 调用 complete_recharge，并写入带来源前缀的原文。"""
    handler, _ = _recorder(_ok_payload())
    _patch_client(monkeypatch, handler)

    order = _order()
    session = _FakeSession(_gateway())
    wallet = _StubWalletService(order)
    monkeypatch.setattr(service, "get_wallet_service", lambda db: wallet)

    result = await service.sync_recharge_order(session, order)

    assert result.outcome is service.QueryOutcome.PAID
    assert order.status is RechargeStatus.SUCCESS
    assert len(wallet.calls) == 1
    call = wallet.calls[0]
    assert call["trade_no"] == order.trade_no
    assert call["epay_trade_no"] == "20261105203743294947"
    assert call["notify_payload"].startswith("主动查询补单")
    assert "20261105203743294947" in call["notify_payload"]


async def test_sync_amount_mismatch_does_not_touch_wallet(monkeypatch):
    """金额不一致：不调 complete_recharge，订单仍是 PENDING。"""
    handler, _ = _recorder(_ok_payload(money="0.01"))
    _patch_client(monkeypatch, handler)

    order = _order()
    session = _FakeSession(_gateway())
    wallet = _StubWalletService(order)
    monkeypatch.setattr(service, "get_wallet_service", lambda db: wallet)

    result = await service.sync_recharge_order(session, order)

    assert result.outcome is service.QueryOutcome.AMOUNT_MISMATCH
    assert wallet.calls == []
    assert order.status is RechargeStatus.PENDING


async def test_sync_unpaid_order_does_not_touch_wallet(monkeypatch):
    """上游未支付：只报告，不入账、不改状态（用户可能稍后才付款）。"""
    handler, _ = _recorder(_ok_payload(status=0, money="0"))
    _patch_client(monkeypatch, handler)

    order = _order()
    session = _FakeSession(_gateway())
    wallet = _StubWalletService(order)
    monkeypatch.setattr(service, "get_wallet_service", lambda db: wallet)

    result = await service.sync_recharge_order(session, order)

    assert result.outcome is service.QueryOutcome.UNPAID
    assert wallet.calls == []
    assert order.status is RechargeStatus.PENDING


async def test_sync_skips_non_pending_order_without_calling_gateway(monkeypatch):
    """已终态的订单不查询、不入账。"""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_ok_payload())

    _patch_client(monkeypatch, handler)

    order = _order(status=RechargeStatus.SUCCESS)
    session = _FakeSession(_gateway())
    wallet = _StubWalletService(order)
    monkeypatch.setattr(service, "get_wallet_service", lambda db: wallet)

    result = await service.sync_recharge_order(session, order)

    assert result.outcome is service.QueryOutcome.UNSUPPORTED
    assert calls == []
    assert wallet.calls == []


async def test_sync_without_gateway_is_unsupported(monkeypatch):
    """历史订单 gateway_id 为 NULL：没有商户号/密钥，无法查询。"""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_ok_payload())

    _patch_client(monkeypatch, handler)

    order = _order(gateway_id=None)
    session = _FakeSession(None)
    wallet = _StubWalletService(order)
    monkeypatch.setattr(service, "get_wallet_service", lambda db: wallet)

    result = await service.sync_recharge_order(session, order)

    assert result.outcome is service.QueryOutcome.UNSUPPORTED
    assert "网关" in result.message
    assert calls == []
    assert wallet.calls == []


# ---------------------------------------------------------------------------
# sync_pending_recharges：批量统计 + 跳过原因
# ---------------------------------------------------------------------------


class _FakeOrderResult:
    def __init__(self, orders: list[RechargeOrder]) -> None:
        self._orders = orders

    def scalars(self):
        return self

    def all(self):
        return list(self._orders)


class _FakeBatchSession:
    """批量扫描只执行一条「取待支付订单」的查询。"""

    def __init__(self, orders: list[RechargeOrder]) -> None:
        self.orders = orders

    async def execute(self, statement):
        return _FakeOrderResult(self.orders)


async def test_sync_pending_summarizes_outcomes_and_skips(monkeypatch):
    """一笔可补单 + 一笔网关停用 + 一笔无网关：分别计入 paid / skipped。"""
    handler, _ = _recorder(_ok_payload())
    _patch_client(monkeypatch, handler)

    paid_order = _order(gateway_id=7)
    disabled_order = _order(trade_no="RCG1NOABCDEF1700000001", gateway_id=8)
    orphan_order = _order(trade_no="RCG1NOABCDEF1700000002", gateway_id=None)

    gateways = {7: _gateway(id=7), 8: _gateway(id=8, enabled=False), None: None}

    async def fake_load_gateway(db, gateway_id):
        return gateways[gateway_id]

    monkeypatch.setattr(service, "_load_gateway", fake_load_gateway)

    wallets: list[_StubWalletService] = []

    def fake_wallet_service(db):
        wallet = _StubWalletService(paid_order)
        wallets.append(wallet)
        return wallet

    monkeypatch.setattr(service, "get_wallet_service", fake_wallet_service)

    session = _FakeBatchSession([paid_order, disabled_order, orphan_order])
    summary = await service.sync_pending_recharges(session)

    assert summary["scanned"] == 3
    assert summary["paid"] == 1
    assert summary["skipped"] == 2
    assert summary["skipped_reasons"] == {
        "gateway_missing": 1,
        "gateway_disabled": 1,
    }
    assert len(wallets) == 1
    assert disabled_order.status is RechargeStatus.PENDING
    assert orphan_order.status is RechargeStatus.PENDING


async def test_sync_pending_isolates_single_order_exception(monkeypatch):
    """单条补单抛异常只计入 errors，不影响同批其它订单。"""
    handler, _ = _recorder(_ok_payload())
    _patch_client(monkeypatch, handler)

    broken_order = _order(trade_no="RCG1NOABCDEF1700000001")
    good_order = _order(trade_no="RCG1NOABCDEF1700000002")

    async def fake_load_gateway(db, gateway_id):
        return _gateway()

    monkeypatch.setattr(service, "_load_gateway", fake_load_gateway)

    async def boom(db, order):
        if order.trade_no == broken_order.trade_no:
            raise RuntimeError("upstream exploded")
        return service.QueryResult(service.QueryOutcome.UNPAID, "未支付")

    monkeypatch.setattr(service, "sync_recharge_order", boom)

    session = _FakeBatchSession([broken_order, good_order])
    summary = await service.sync_pending_recharges(session)

    assert summary["scanned"] == 2
    assert summary["errors"] == 1
    assert summary["unpaid"] == 1
    assert summary["paid"] == 0
