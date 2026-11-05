"""支付网关地址解析与回调兼容口径的纯单元测试（无数据库、无网络）。

重点覆盖「老板自己填地址」最容易出错的几种情况：
- 勾了自动补 .php 时，基地址/已带 .php/以 /submit 结尾三种写法；
- 不勾时完整入口原样使用；
- 只填域名必须报错（线上实测：域名根路径下可能恰好有另一个 submit.php，
  填错不会 404，而是打到别的商户应用）；
- 回调验签要兼容老易支付与立收通两种签名口径。
"""

import hashlib
from decimal import Decimal

import pytest

from app.models.payment_gateway import PaymentGateway
from app.services import payment_gateway_service as pg
from app.services import recharge_service


def _gateway(**overrides) -> PaymentGateway:
    data = {
        "name": "测试网关",
        "submit_url": "https://pay.example.com/xpay/epay/",
        "append_submit_php": True,
        "merchant_id": "1001",
        "secret_key": "k",
        "alipay_enabled": True,
        "wxpay_enabled": True,
        "enabled": True,
        "is_active": True,
    }
    data.update(overrides)
    return PaymentGateway(**data)


# ---------------------------------------------------------------------------
# 提交地址
# ---------------------------------------------------------------------------


def test_append_mode_builds_submit_php_from_base_directory():
    assert (
        pg.normalize_submit_url("https://pay.example.com/xpay/epay/", append_submit_php=True)
        == "https://pay.example.com/xpay/epay/submit.php"
    )


def test_append_mode_keeps_address_that_already_has_php():
    assert (
        pg.normalize_submit_url(
            "https://pay.example.com/xpay/epay/submit.php", append_submit_php=True
        )
        == "https://pay.example.com/xpay/epay/submit.php"
    )


def test_append_mode_completes_submit_without_php():
    assert (
        pg.normalize_submit_url("https://pay.example.com/epay/submit", append_submit_php=True)
        == "https://pay.example.com/epay/submit.php"
    )


def test_full_endpoint_mode_uses_address_as_is():
    assert (
        pg.normalize_submit_url(
            "https://api.lspay.tech/gateway/submit", append_submit_php=False
        )
        == "https://api.lspay.tech/gateway/submit"
    )


def test_full_endpoint_mode_strips_trailing_slash():
    assert (
        pg.normalize_submit_url("https://api.lspay.tech/gateway/submit/", append_submit_php=False)
        == "https://api.lspay.tech/gateway/submit"
    )


@pytest.mark.parametrize(
    "raw,append",
    [
        ("https://pay.example.com", True),
        ("https://pay.example.com/", True),
        ("https://pay.example.com", False),
    ],
)
def test_domain_only_is_rejected(raw: str, append: bool):
    """只填域名必须报错：根路径下可能有另一个商户入口，静默走错更危险。"""
    with pytest.raises(pg.GatewayAddressError) as exc:
        pg.normalize_submit_url(raw, append_submit_php=append)
    assert "缺少支付路径" in str(exc.value)


@pytest.mark.parametrize(
    "raw",
    [
        "http://pay.example.com/epay",
        "https://pay.example.com/epay?pid=1",
        "https://user:pass@pay.example.com/epay",
        "",
    ],
)
def test_invalid_addresses_are_rejected(raw: str):
    with pytest.raises(pg.GatewayAddressError):
        pg.normalize_submit_url(raw, append_submit_php=True)


# ---------------------------------------------------------------------------
# 查询地址
# ---------------------------------------------------------------------------


def test_query_url_is_derived_only_for_epay_layout():
    assert (
        pg.resolve_query_url(_gateway())
        == "https://pay.example.com/xpay/epay/api.php"
    )


def test_full_endpoint_gateway_has_no_query_url_by_default():
    gateway = _gateway(
        submit_url="https://api.lspay.tech/gateway/submit",
        append_submit_php=False,
        query_url=None,
    )
    assert pg.resolve_query_url(gateway) is None


def test_explicit_query_url_wins():
    gateway = _gateway(query_url="https://pay.example.com/custom/query")
    assert pg.resolve_query_url(gateway) == "https://pay.example.com/custom/query"


# ---------------------------------------------------------------------------
# 回调验签口径
# ---------------------------------------------------------------------------


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest().upper()


def test_notify_accepts_legacy_all_fields_signature():
    params = {
        "pid": "1001",
        "trade_no": "EPAY-9",
        "out_trade_no": "RCG1NOT1",
        "type": "alipay",
        "name": "账户充值",
        "money": "100.00",
        "trade_status": "TRADE_SUCCESS",
        "sign_type": "MD5",
    }
    raw = "&".join(f"{k}={params[k]}" for k in sorted(params) if k not in ("sign", "sign_type"))
    params["sign"] = hashlib.md5(f"{raw}k".encode("utf-8")).hexdigest()
    assert recharge_service._notify_signature_ok(params, _gateway()) is True


def test_notify_accepts_whitelist_uppercase_signature():
    """立收通兼容接口：只签七个字段、MD5 大写。"""
    params = {
        "pid": "1001",
        "type": "alipay",
        "out_trade_no": "RCG1NOT1",
        "money": "100.00",
        "name": "账户充值",
        "notify_url": "https://x/api/v1/wallet/recharge/notify",
        "return_url": "https://x/wallet",
        # 回调里多出来的字段不参与签名
        "trade_no": "EPAY-9",
        "trade_status": "TRADE_SUCCESS",
        "add_time": "2026-11-05 21:00:00",
    }
    raw = "&".join(f"{k}={params[k]}" for k in sorted(recharge_service._LSPAY_SIGN_FIELDS))
    params["sign"] = _md5(f"{raw}k")
    assert recharge_service._notify_signature_ok(params, _gateway()) is True


def test_notify_rejects_wrong_key_and_missing_sign():
    params = {"out_trade_no": "T1", "money": "1.00", "sign": _md5("whatever")}
    assert recharge_service._notify_signature_ok(params, _gateway()) is False
    assert recharge_service._notify_signature_ok({"out_trade_no": "T1"}, _gateway()) is False


def test_notify_paid_recognises_both_protocols():
    assert recharge_service._notify_is_paid({"trade_status": "TRADE_SUCCESS"}) is True
    assert recharge_service._notify_is_paid({"trade_status": "WAIT_BUYER_PAY"}) is False
    assert recharge_service._notify_is_paid({"state": "2"}) is True
    assert recharge_service._notify_is_paid({"state": 1}) is False


def test_notify_amount_handles_yuan_and_cents():
    assert recharge_service._notify_amount({"money": "12.34"}) == Decimal("12.34")
    # 立收通原生按分返回
    assert recharge_service._notify_amount({"amount": "1234"}) == Decimal("12.34")
    assert recharge_service._notify_amount({}) is None
