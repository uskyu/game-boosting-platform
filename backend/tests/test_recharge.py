"""易支付充值接口测试：配置读写、下单、回调幂等与安全校验。

覆盖重点：
- 管理员配置接口绝不回传商户密钥；
- 充值回调重复投递只入账一次（幂等）；
- 签名错误、金额被篡改、未知订单一律拒绝；
- 充值只进可用余额，不影响累计收入，也不改变既有托管/提现语义。
"""

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.recharge import RechargeOrder, RechargeStatus
from app.models.wallet import Wallet, WalletTransaction, WalletTransactionType
from app.services import epay_service
from tests.conftest import auth_header

PAY_ADDRESS = "https://pay.example.com"
EPAY_ID = "1001"
EPAY_KEY = "unit-test-secret"
NOTIFY_BASE = "https://platform.example.com"
NOTIFY_PATH = "/wallet/recharge/notify"


def _settings_payload(**overrides) -> dict:
    payload = {
        "enabled": True,
        "pay_address": PAY_ADDRESS,
        "epay_id": EPAY_ID,
        "epay_key": EPAY_KEY,
        "notify_base_url": NOTIFY_BASE,
        "pay_methods": '[{"name": "支付宝", "type": "alipay"}]',
        "min_amount": 1,
    }
    payload.update(overrides)
    return payload


async def _configure(client: AsyncClient, admin_user: dict, **overrides):
    resp = await client.put(
        "/admin/payment/settings",
        json=_settings_payload(**overrides),
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _notify_params(
    trade_no: str,
    money: str,
    *,
    key: str = EPAY_KEY,
    trade_status: str = "TRADE_SUCCESS",
    epay_trade_no: str = "EPAY-1",
) -> dict[str, str]:
    """构造一份带合法签名的易支付回调参数。"""
    return epay_service.generate_params(
        {
            "pid": EPAY_ID,
            "trade_no": epay_trade_no,
            "out_trade_no": trade_no,
            "type": "alipay",
            "name": "账户充值",
            "money": money,
            "trade_status": trade_status,
        },
        key,
    )


async def _create_order(
    client: AsyncClient, user: dict, amount: float = 100, method: str = "alipay"
):
    resp = await client.post(
        "/wallet/recharge",
        json={"amount": amount, "payment_method": method},
        headers=auth_header(user),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 管理员配置
# ---------------------------------------------------------------------------


async def test_admin_settings_never_returns_secret_key(
    client: AsyncClient, admin_user: dict
):
    data = await _configure(client, admin_user)
    assert data["has_key"] is True
    assert data["epay_id"] == EPAY_ID
    assert data["pay_address"] == PAY_ADDRESS
    # 密钥字段不得出现在响应里
    assert "epay_key" not in data


async def test_admin_settings_keeps_key_when_omitted(
    client: AsyncClient, admin_user: dict
):
    await _configure(client, admin_user)

    # 留空 epay_key 再保存一次：密钥应保持不变
    resp = await client.put(
        "/admin/payment/settings",
        json=_settings_payload(epay_key=None, epay_id="2002"),
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    assert resp.json()["has_key"] is True
    assert resp.json()["epay_id"] == "2002"

    # 空字符串同样表示不修改
    resp = await client.put(
        "/admin/payment/settings",
        json=_settings_payload(epay_key="", epay_id="2002"),
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    assert resp.json()["has_key"] is True


async def test_admin_settings_rejects_invalid_pay_methods_json(
    client: AsyncClient, admin_user: dict
):
    resp = await client.put(
        "/admin/payment/settings",
        json=_settings_payload(pay_methods="not-json"),
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 422
    assert "JSON" in resp.text


async def test_admin_settings_requires_admin(
    client: AsyncClient, registered_user: dict
):
    resp = await client.put(
        "/admin/payment/settings",
        json=_settings_payload(),
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 充值入口配置
# ---------------------------------------------------------------------------


async def test_recharge_config_disabled_before_credentials(
    client: AsyncClient, registered_user: dict
):
    resp = await client.get(
        "/wallet/recharge/config", headers=auth_header(registered_user)
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["pay_methods"] == []


async def test_recharge_config_enabled_after_credentials(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    await _configure(client, admin_user)
    resp = await client.get(
        "/wallet/recharge/config", headers=auth_header(registered_user)
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["pay_methods"] == [{"name": "支付宝", "type": "alipay"}]
    assert Decimal(str(data["min_amount"])) == Decimal("1.00")
    # 配置接口同样不得泄漏密钥
    assert "epay_key" not in data


async def test_recharge_config_requires_login(client: AsyncClient):
    resp = await client.get("/wallet/recharge/config")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 发起充值
# ---------------------------------------------------------------------------


async def test_create_recharge_returns_signed_form(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    await _configure(client, admin_user)
    data = await _create_order(client, registered_user, amount=100)

    assert data["trade_no"].startswith("RCG")
    assert Decimal(str(data["amount"])) == Decimal("100.00")
    assert data["pay_url"] == f"{PAY_ADDRESS}/submit.php"

    params = data["params"]
    assert params["pid"] == EPAY_ID
    assert params["type"] == "alipay"
    assert params["out_trade_no"] == data["trade_no"]
    assert params["money"] == "100.00"
    assert params["device"] == "pc"
    assert params["sign_type"] == "MD5"
    # 回调地址取自管理员配置的回调基础地址
    assert params["notify_url"] == f"{NOTIFY_BASE}/api/v1{NOTIFY_PATH}"
    assert params["return_url"] == f"{NOTIFY_BASE}/wallet"
    # 签名可被独立校验
    assert epay_service.verify_params(params, EPAY_KEY)["verify_status"] is True


async def test_create_recharge_persists_pending_order(
    client: AsyncClient, admin_user: dict, registered_user: dict, db_session
):
    await _configure(client, admin_user)
    data = await _create_order(client, registered_user, amount=50)

    result = await db_session.execute(
        select(RechargeOrder).where(RechargeOrder.trade_no == data["trade_no"])
    )
    order = result.scalar_one()
    assert order.status == RechargeStatus.PENDING
    assert Decimal(str(order.amount)) == Decimal("50.00")
    assert order.paid_at is None


@pytest.mark.parametrize(
    "amount,method,expected",
    [
        (100, "unionpay", "支付方式不存在"),
        (0.5, "alipay", "充值金额不能低于"),
    ],
)
async def test_create_recharge_rejects_invalid_input(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    amount,
    method,
    expected,
):
    await _configure(client, admin_user)
    resp = await client.post(
        "/wallet/recharge",
        json={"amount": amount, "payment_method": method},
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 400
    assert expected in resp.json()["detail"]


async def test_create_recharge_rejected_when_disabled(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    await _configure(client, admin_user, enabled=False)
    resp = await client.post(
        "/wallet/recharge",
        json={"amount": 100, "payment_method": "alipay"},
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 400
    assert "未开启" in resp.json()["detail"]


async def test_admin_cannot_recharge(
    client: AsyncClient, admin_user: dict
):
    await _configure(client, admin_user)
    resp = await client.post(
        "/wallet/recharge",
        json={"amount": 100, "payment_method": "alipay"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 回调入账（核心：幂等）
# ---------------------------------------------------------------------------


async def test_notify_credits_wallet(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    await _configure(client, admin_user)
    created = await _create_order(client, registered_user, amount=100)

    resp = await client.post(
        NOTIFY_PATH,
        data=_notify_params(created["trade_no"], "100.00"),
    )
    assert resp.status_code == 200
    assert resp.text == "success"

    wallet = await client.get("/wallet", headers=auth_header(registered_user))
    assert Decimal(str(wallet.json()["available_balance"])) == Decimal("100.00")
    # 充值不是收入，累计收入保持不变
    assert Decimal(str(wallet.json()["total_income"])) == Decimal("0.00")


async def test_repeated_notify_credits_only_once(
    client: AsyncClient, admin_user: dict, registered_user: dict, db_session
):
    """易支付会重复投递回调，重复请求必须仍然回 success 但不能重复加钱。"""
    await _configure(client, admin_user)
    created = await _create_order(client, registered_user, amount=100)
    params = _notify_params(created["trade_no"], "100.00")

    for _ in range(3):
        resp = await client.post(NOTIFY_PATH, data=params)
        assert resp.status_code == 200
        assert resp.text == "success"

    wallet = await client.get("/wallet", headers=auth_header(registered_user))
    assert Decimal(str(wallet.json()["available_balance"])) == Decimal("100.00")

    # 流水只有一条 RECHARGE
    result = await db_session.execute(
        select(WalletTransaction).where(
            WalletTransaction.type == WalletTransactionType.RECHARGE
        )
    )
    entries = result.scalars().all()
    assert len(entries) == 1
    assert Decimal(str(entries[0].amount)) == Decimal("100.00")


async def test_notify_get_method_also_works(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    await _configure(client, admin_user)
    created = await _create_order(client, registered_user, amount=30)

    resp = await client.get(
        NOTIFY_PATH, params=_notify_params(created["trade_no"], "30.00")
    )
    assert resp.status_code == 200
    assert resp.text == "success"

    wallet = await client.get("/wallet", headers=auth_header(registered_user))
    assert Decimal(str(wallet.json()["available_balance"])) == Decimal("30.00")


async def test_notify_rejects_bad_signature(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    await _configure(client, admin_user)
    created = await _create_order(client, registered_user, amount=100)

    params = _notify_params(created["trade_no"], "100.00", key="wrong-key")
    resp = await client.post(NOTIFY_PATH, data=params)
    assert resp.status_code == 200
    assert resp.text == "fail"

    wallet = await client.get("/wallet", headers=auth_header(registered_user))
    assert Decimal(str(wallet.json()["available_balance"])) == Decimal("0.00")


async def test_notify_rejects_tampered_amount(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    """签名合法但金额与订单不一致（例如上游异常）必须拒绝入账。"""
    await _configure(client, admin_user)
    created = await _create_order(client, registered_user, amount=100)

    # 用正确密钥对 1.00 元重新签名：签名有效但金额不等于订单金额
    params = _notify_params(created["trade_no"], "1.00")
    resp = await client.post(NOTIFY_PATH, data=params)
    assert resp.status_code == 200
    assert resp.text == "fail"

    wallet = await client.get("/wallet", headers=auth_header(registered_user))
    assert Decimal(str(wallet.json()["available_balance"])) == Decimal("0.00")


async def test_notify_rejects_unknown_order(
    client: AsyncClient, admin_user: dict
):
    await _configure(client, admin_user)
    params = _notify_params("RCG999NONEXISTENT", "100.00")
    resp = await client.post(NOTIFY_PATH, data=params)
    assert resp.status_code == 200
    assert resp.text == "fail"


async def test_notify_ignores_non_success_status(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    await _configure(client, admin_user)
    created = await _create_order(client, registered_user, amount=100)

    params = _notify_params(
        created["trade_no"], "100.00", trade_status="TRADE_PENDING"
    )
    resp = await client.post(NOTIFY_PATH, data=params)
    assert resp.status_code == 200
    assert resp.text == "fail"

    wallet = await client.get("/wallet", headers=auth_header(registered_user))
    assert Decimal(str(wallet.json()["available_balance"])) == Decimal("0.00")


async def test_notify_rejects_empty_params(client: AsyncClient):
    resp = await client.post(NOTIFY_PATH, data={})
    assert resp.status_code == 200
    assert resp.text == "fail"


# ---------------------------------------------------------------------------
# 充值记录
# ---------------------------------------------------------------------------


async def test_my_recharges_lists_own_orders(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    await _configure(client, admin_user)
    created = await _create_order(client, registered_user, amount=100)
    await client.post(
        NOTIFY_PATH, data=_notify_params(created["trade_no"], "100.00")
    )

    resp = await client.get(
        "/wallet/recharge/mine", headers=auth_header(registered_user)
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["trade_no"] == created["trade_no"]
    assert item["status"] == "SUCCESS"
    assert item["paid_at"] is not None
    assert Decimal(str(item["amount"])) == Decimal("100.00")


async def test_my_recharges_only_shows_own_orders(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
):
    await _configure(client, admin_user)
    await _create_order(client, registered_user, amount=100)

    resp = await client.get(
        "/wallet/recharge/mine", headers=auth_header(booster_user)
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


async def test_recharge_does_not_touch_other_wallet_fields(
    client: AsyncClient, admin_user: dict, registered_user: dict, db_session
):
    await _configure(client, admin_user)
    created = await _create_order(client, registered_user, amount=88)
    await client.post(NOTIFY_PATH, data=_notify_params(created["trade_no"], "88.00"))

    user_id = registered_user["user"]["id"]
    result = await db_session.execute(
        select(Wallet).where(Wallet.user_id == user_id)
    )
    wallet = result.scalar_one()
    assert Decimal(str(wallet.available_balance)) == Decimal("88.00")
    assert Decimal(str(wallet.frozen_balance)) == Decimal("0.00")
    assert Decimal(str(wallet.total_income)) == Decimal("0.00")
    assert Decimal(str(wallet.total_withdrawn)) == Decimal("0.00")
