"""易支付（Epay）协议实现。

协议逻辑移植自 New API 使用的 github.com/Calcium-Ion/go-epay v0.0.4，
签名规则完全一致：

1. 剔除 ``sign`` / ``sign_type`` 以及值为空的参数；
2. 剩余参数按 key 升序排列，拼成 ``k1=v1&k2=v2``；
3. 末尾追加商户密钥，做 MD5 得到签名。

本模块只做纯计算（无 IO、无数据库），便于直接单测。
"""

import hashlib
import hmac
import secrets
import string
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# 上游返回的支付成功状态
TRADE_SUCCESS = "TRADE_SUCCESS"

SIGN_TYPE = "MD5"
PURCHASE_PATH = "/submit.php"

# 拉起支付时使用的设备类型
DEVICE_PC = "pc"

_ORDER_ALPHABET = string.ascii_uppercase + string.digits


class EpayError(ValueError):
    """易支付参数或配置错误。"""


def params_filter(params: dict[str, str]) -> dict[str, str]:
    """签名前过滤参数：去掉 sign / sign_type 和空值。"""
    return {
        key: value
        for key, value in params.items()
        if key not in ("sign", "sign_type") and value not in (None, "")
    }


def params_sort(params: dict[str, str]) -> tuple[list[str], list[str]]:
    """按 key 升序排列，返回 (keys, values)。"""
    keys = sorted(params.keys())
    return keys, [params[key] for key in keys]


def create_url_string(keys: list[str], values: list[str]) -> str:
    """把 keys/values 拼成待签名字符串：``a=d&b=e&c=f``。"""
    return "&".join(f"{key}={value}" for key, value in zip(keys, values))


def md5_string(url_string: str, key: str) -> str:
    """加盐（商户密钥）MD5。"""
    return hashlib.md5(f"{url_string}{key}".encode("utf-8")).hexdigest()


def generate_sign(params: dict[str, str], key: str) -> str:
    """按易支付规范生成签名。"""
    filtered = params_filter(params)
    keys, values = params_sort(filtered)
    return md5_string(create_url_string(keys, values), key)


def generate_params(params: dict[str, str], key: str) -> dict[str, str]:
    """在参数上补全 ``sign`` 与 ``sign_type``。"""
    result = dict(params)
    result["sign"] = generate_sign(params, key)
    result["sign_type"] = SIGN_TYPE
    return result


def build_purchase(
    *,
    base_url: str,
    partner_id: str,
    key: str,
    pay_type: str,
    out_trade_no: str,
    name: str,
    money: Decimal | str,
    notify_url: str,
    return_url: str,
    device: str = DEVICE_PC,
) -> tuple[str, dict[str, str]]:
    """构造支付地址与表单参数（前端以 POST 表单提交）。

    Returns:
        (pay_url, params)：pay_url 指向 ``{base_url}/submit.php``；
        params 已包含签名，直接作为隐藏表单字段提交。
    """
    if not base_url:
        raise EpayError("支付接口地址未配置")
    if not partner_id or not key:
        raise EpayError("易支付商户信息未配置")

    pay_url = base_url.rstrip("/") + PURCHASE_PATH
    params = {
        "pid": partner_id,
        "type": pay_type,
        "out_trade_no": out_trade_no,
        "notify_url": notify_url,
        "name": name,
        "money": format_money(money),
        "device": device,
        "sign_type": SIGN_TYPE,
        "return_url": return_url,
        "sign": "",
    }
    return pay_url, generate_params(params, key)


def verify_params(params: dict[str, str], key: str) -> dict:
    """校验回调签名并解析关键字段。

    返回字典始终包含 ``verify_status``（签名是否正确），
    以及 ``trade_no`` / ``out_trade_no`` / ``type`` / ``name`` /
    ``money`` / ``trade_status``。签名不匹配时 ``verify_status`` 为 False。
    """
    provided = params.get("sign") or ""
    expected = generate_sign(params, key)
    # 常量时间比较，避免签名校验被计时侧信道利用
    return {
        "verify_status": hmac.compare_digest(provided, expected),
        "trade_no": params.get("trade_no") or "",
        "out_trade_no": params.get("out_trade_no") or "",
        "type": params.get("type") or "",
        "name": params.get("name") or "",
        "money": params.get("money") or "",
        "trade_status": params.get("trade_status") or "",
    }


def parse_money(value: str | Decimal | None) -> Decimal | None:
    """把金额解析为 Decimal（两位小数，四舍五入）；无法解析时返回 None。

    与 wallet_service 保持一致使用 ROUND_HALF_UP，避免银行家舍入造成
    与订单金额比对时出现意外差异。
    """
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def format_money(value: Decimal | str | int | float) -> str:
    """金额格式化为两位小数字符串（易支付要求）。"""
    parsed = parse_money(value)
    if parsed is None:
        raise EpayError("支付金额无效")
    return f"{parsed:.2f}"


def generate_trade_no(user_id: int, prefix: str = "RCG") -> str:
    """商户订单号：``{prefix}{user_id}NO{6位随机}{unix秒}``。

    与 New API 的 ``USR{id}NO...`` 命名规则同源，便于上游对账时定位用户。
    """
    suffix = "".join(secrets.choice(_ORDER_ALPHABET) for _ in range(6))
    return f"{prefix}{user_id}NO{suffix}{int(time.time())}"
