"""易支付签名协议的纯单元测试（无数据库、无 IO）。

签名规则：剔除 sign/sign_type/空值 → 按 key 升序拼 k=v&… → 追加商户密钥 → MD5。
测试侧用一份独立实现做交叉验证，避免「用被测代码验证被测代码」。
"""

import hashlib
from decimal import Decimal

import pytest

from app.services import epay_service


def _independent_sign(params: dict[str, str], key: str) -> str:
    """测试侧独立实现的签名算法，不复用被测代码。"""
    filtered = {
        name: value
        for name, value in params.items()
        if name not in ("sign", "sign_type") and value not in (None, "")
    }
    canonical = "&".join(f"{name}={filtered[name]}" for name in sorted(filtered))
    return hashlib.md5((canonical + key).encode("utf-8")).hexdigest()


def test_params_filter_drops_sign_sign_type_and_empty_values():
    params = {"a": "1", "sign": "x", "sign_type": "MD5", "b": "", "c": "3"}
    assert epay_service.params_filter(params) == {"a": "1", "c": "3"}


def test_params_sort_is_ascending_by_key():
    keys, values = epay_service.params_sort({"b": "2", "a": "1", "c": "3"})
    assert keys == ["a", "b", "c"]
    assert values == ["1", "2", "3"]


def test_create_url_string_joins_with_ampersand_and_drops_trailing():
    assert epay_service.create_url_string(["a", "b", "c"], ["1", "2", "3"]) == "a=1&b=2&c=3"


def test_generate_sign_matches_independent_implementation():
    params = {
        "pid": "1001",
        "type": "alipay",
        "out_trade_no": "RCG1NOABC123",
        "notify_url": "https://example.com/api/v1/wallet/recharge/notify",
        "name": "账户充值 100.00元",
        "money": "100.00",
        "device": "pc",
        "sign_type": "MD5",
        "return_url": "https://example.com/wallet",
        "sign": "",
    }
    key = "test-secret-key"
    assert epay_service.generate_sign(params, key) == _independent_sign(params, key)


def test_sign_is_lowercase_md5_hex():
    sign = epay_service.generate_sign({"pid": "1"}, "k")
    assert len(sign) == 32
    assert all(ch in "0123456789abcdef" for ch in sign)


def test_empty_values_do_not_change_signature():
    key = "k"
    assert epay_service.generate_sign({"a": "1", "b": ""}, key) == epay_service.generate_sign(
        {"a": "1"}, key
    )


def test_different_keys_produce_different_signatures():
    params = {"pid": "1", "money": "10.00"}
    assert epay_service.generate_sign(params, "key-a") != epay_service.generate_sign(
        params, "key-b"
    )


def test_generate_params_adds_sign_and_sign_type():
    result = epay_service.generate_params({"pid": "1"}, "k")
    assert result["sign_type"] == "MD5"
    assert result["sign"] == epay_service.generate_sign({"pid": "1"}, "k")


def test_verify_params_accepts_valid_signature():
    key = "k"
    params = epay_service.generate_params(
        {
            "trade_no": "EPAY-1",
            "out_trade_no": "RCG1NOT1",
            "money": "10.00",
            "trade_status": "TRADE_SUCCESS",
        },
        key,
    )
    verified = epay_service.verify_params(params, key)
    assert verified["verify_status"] is True
    assert verified["out_trade_no"] == "RCG1NOT1"
    assert verified["trade_no"] == "EPAY-1"
    assert verified["trade_status"] == "TRADE_SUCCESS"


def test_verify_params_rejects_tampered_money():
    key = "k"
    params = epay_service.generate_params(
        {"out_trade_no": "RCG1NOT1", "money": "10.00"}, key
    )
    params["money"] = "9999.00"
    assert epay_service.verify_params(params, key)["verify_status"] is False


def test_verify_params_rejects_wrong_key():
    params = epay_service.generate_params({"out_trade_no": "RCG1NOT1"}, "real-key")
    assert epay_service.verify_params(params, "other-key")["verify_status"] is False


def test_verify_params_without_sign_is_false():
    assert epay_service.verify_params({"out_trade_no": "T1"}, "k")["verify_status"] is False


def test_build_purchase_returns_submit_php_url_and_signed_params():
    url, params = epay_service.build_purchase(
        base_url="https://pay.example.com",
        partner_id="1001",
        key="k",
        pay_type="alipay",
        out_trade_no="RCG1NOT1",
        name="账户充值 100.00元",
        money=Decimal("100"),
        notify_url="https://x/api/v1/wallet/recharge/notify",
        return_url="https://x/wallet",
    )
    assert url == "https://pay.example.com/submit.php"
    assert params["money"] == "100.00"
    assert params["pid"] == "1001"
    assert params["type"] == "alipay"
    assert params["device"] == "pc"
    assert params["sign_type"] == "MD5"
    # 签名必须与独立实现一致
    assert params["sign"] == _independent_sign(params, "k")


def test_build_purchase_tolerates_trailing_slash():
    url, _ = epay_service.build_purchase(
        base_url="https://pay.example.com/",
        partner_id="1",
        key="k",
        pay_type="alipay",
        out_trade_no="T1",
        name="n",
        money=Decimal("1.00"),
        notify_url="https://x/n",
        return_url="https://x/r",
    )
    assert url == "https://pay.example.com/submit.php"


@pytest.mark.parametrize(
    "base_url,partner_id,key",
    [
        ("", "1", "k"),
        ("https://pay.example.com", "", "k"),
        ("https://pay.example.com", "1", ""),
    ],
)
def test_build_purchase_requires_full_credentials(base_url, partner_id, key):
    with pytest.raises(epay_service.EpayError):
        epay_service.build_purchase(
            base_url=base_url,
            partner_id=partner_id,
            key=key,
            pay_type="alipay",
            out_trade_no="T1",
            name="n",
            money=Decimal("1.00"),
            notify_url="https://x/n",
            return_url="https://x/r",
        )


def test_format_money_always_two_decimals():
    assert epay_service.format_money(Decimal("100")) == "100.00"
    assert epay_service.format_money("99.9") == "99.90"
    assert epay_service.format_money(1) == "1.00"


def test_parse_money_normalizes_scale_and_rejects_garbage():
    assert epay_service.parse_money("100.000") == Decimal("100.00")
    assert epay_service.parse_money("100.005") == Decimal("100.01")
    assert epay_service.parse_money("") is None
    assert epay_service.parse_money(None) is None
    assert epay_service.parse_money("abc") is None


def test_generate_trade_no_shape():
    trade_no = epay_service.generate_trade_no(42)
    assert trade_no.startswith("RCG42NO")
    # RCG + 42 + NO + 6 位随机 + unix 秒
    assert len(trade_no) > len("RCG42NO") + 6
    suffix = trade_no[len("RCG42NO"):]
    assert suffix[:6].isalnum()
    assert suffix[6:].isdigit()


def test_generate_trade_no_is_unique_across_calls():
    numbers = {epay_service.generate_trade_no(1) for _ in range(50)}
    assert len(numbers) == 50
