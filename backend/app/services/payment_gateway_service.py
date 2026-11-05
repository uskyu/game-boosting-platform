"""支付网关配置的读写与地址解析。

每个网关一行，管理员自行增删改，并指定「当前启用」的一家。这里集中处理：

- 提交地址的校验与规范化（是否自动补 ``/submit.php``、只填域名要报错）；
- 订单查询地址的推导（只有易支付标准布局才敢自动推导 ``/api.php``）；
- 当前启用网关的唯一性（同一事务内先全部置否，再置是）。

密钥只在此模块与管理员接口之间流转，绝不下发到用户侧。
"""

import logging
from urllib.parse import urlparse

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment_gateway import PaymentGateway
from app.models.recharge import RechargeOrder

logger = logging.getLogger(__name__)

__all__ = [
    "GatewayAddressError",
    "activate_gateway",
    "create_gateway",
    "delete_gateway",
    "get_active_gateway",
    "get_gateway",
    "list_gateways",
    "normalize_submit_url",
    "resolve_query_url",
    "resolve_submit_url",
    "update_gateway",
]

SUBMIT_SCRIPT = "submit.php"
QUERY_SCRIPT = "api.php"


class GatewayAddressError(ValueError):
    """提交地址不合法（面向管理员的校验错误）。"""


def _parse_https_url(raw: str, *, field: str) -> urlparse:
    """解析并校验一条 https 地址，返回解析结果。"""
    text = (raw or "").strip()
    if not text:
        raise GatewayAddressError(f"{field}不能为空")

    parsed = urlparse(text)
    if parsed.scheme != "https":
        raise GatewayAddressError(f"{field}必须以 https:// 开头")
    if not parsed.netloc:
        raise GatewayAddressError(f"{field}缺少域名")
    if parsed.query or parsed.fragment:
        raise GatewayAddressError(f"{field}不能带查询参数或锚点")
    if parsed.username or parsed.password:
        raise GatewayAddressError(f"{field}不能包含账号密码")

    # 只有域名、没有路径：几乎一定是漏填了支付目录，直接拒绝。
    # 有些站点根目录下恰好也有 /submit.php，填错不会 404，而是打到别的应用，
    # 所以这里必须拦住，不能靠「跑一次看看」。
    if parsed.path in ("", "/"):
        raise GatewayAddressError(
            f"{field}缺少支付路径，例如 https://pay.example.com/xpay/epay "
            "或 https://api.example.com/gateway/submit"
        )
    return parsed


def normalize_submit_url(raw: str, *, append_submit_php: bool) -> str:
    """把管理员填写的地址规范化成最终提交地址。

    勾选 ``append_submit_php`` 时按基地址理解：已带 ``.php`` 原样保留，
    以 ``/submit`` 结尾的补 ``.php``，其余补 ``/submit.php``；
    不勾选时按完整入口原样使用（去掉尾部斜杠）。
    """
    parsed = _parse_https_url(raw, field="提交地址")
    base = f"https://{parsed.netloc}{parsed.path}"
    base = base.rstrip("/")

    if not append_submit_php:
        return base

    lowered = base.lower()
    if lowered.endswith(".php"):
        return base
    if lowered.endswith("/submit"):
        return f"{base}.php"
    return f"{base}/{SUBMIT_SCRIPT}"


def normalize_query_url(raw: str) -> str:
    """规范化订单查询地址（同样是完整 https 地址）。"""
    parsed = _parse_https_url(raw, field="查询地址")
    return f"https://{parsed.netloc}{parsed.path}".rstrip("/")


def resolve_submit_url(gateway: PaymentGateway) -> str:
    """网关最终使用的提交地址。"""
    return normalize_submit_url(
        gateway.submit_url, append_submit_php=bool(gateway.append_submit_php)
    )


def resolve_query_url(gateway: PaymentGateway) -> str | None:
    """网关的订单查询地址；不支持查询时返回 ``None``。

    显式填写优先。未填写时只对「易支付标准布局」（需要补 submit.php 的
    网关）自动推导 ``{基地址}/api.php``——实测该布局可用；
    其余网关不臆造路径，视为不支持主动查询。
    """
    if (gateway.query_url or "").strip():
        return normalize_query_url(gateway.query_url or "")
    if not gateway.append_submit_php:
        return None
    parsed = urlparse((gateway.submit_url or "").strip())
    if not parsed.netloc:
        return None
    base = f"https://{parsed.netloc}{parsed.path}".rstrip("/")
    if base.lower().endswith(".php"):
        base = base[: -len("/submit.php")] if base.lower().endswith("/submit.php") else base
    return f"{base}/{QUERY_SCRIPT}"


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------


async def list_gateways(db: AsyncSession) -> list[PaymentGateway]:
    """全部网关，当前启用的排最前，其余按创建顺序。"""
    result = await db.execute(
        select(PaymentGateway).order_by(
            PaymentGateway.is_active.desc(), PaymentGateway.id.asc()
        )
    )
    return list(result.scalars().all())


async def get_gateway(db: AsyncSession, gateway_id: int) -> PaymentGateway:
    result = await db.execute(
        select(PaymentGateway).where(PaymentGateway.id == gateway_id)
    )
    gateway = result.scalar_one_or_none()
    if gateway is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="支付网关不存在"
        )
    return gateway


async def get_active_gateway(db: AsyncSession) -> PaymentGateway | None:
    """当前启用的网关；未配置或未启用时返回 ``None``。"""
    result = await db.execute(
        select(PaymentGateway).where(PaymentGateway.is_active.is_(True)).limit(1)
    )
    gateway = result.scalar_one_or_none()
    if gateway is None or not gateway.enabled:
        return None
    return gateway


async def _clear_active(db: AsyncSession, *, except_id: int | None = None) -> None:
    """把其它网关的 is_active 全部置否（同事务内保证唯一）。"""
    stmt = update(PaymentGateway).where(PaymentGateway.is_active.is_(True))
    if except_id is not None:
        stmt = stmt.where(PaymentGateway.id != except_id)
    await db.execute(stmt.values(is_active=False))


def _validate_gateway_fields(
    *,
    name: str,
    submit_url: str,
    append_submit_php: bool,
    merchant_id: str,
    query_url: str | None,
) -> tuple[str, str, str | None]:
    """校验并返回 (name, submit_url 原文, query_url)；地址错误抛 400。"""
    name = (name or "").strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="网关名称不能为空"
        )
    merchant_id = (merchant_id or "").strip()
    if not merchant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="商户号不能为空"
        )

    submit_url = (submit_url or "").strip()
    try:
        normalize_submit_url(submit_url, append_submit_php=append_submit_php)
    except GatewayAddressError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)
        ) from err

    normalized_query = (query_url or "").strip() or None
    if normalized_query:
        try:
            normalize_query_url(normalized_query)
        except GatewayAddressError as err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)
            ) from err
    return name, submit_url, normalized_query


async def create_gateway(
    db: AsyncSession,
    *,
    name: str,
    submit_url: str,
    append_submit_php: bool,
    merchant_id: str,
    secret_key: str,
    query_url: str | None = None,
    alipay_enabled: bool = True,
    wxpay_enabled: bool = True,
    enabled: bool = True,
) -> PaymentGateway:
    """新增一个网关。库里还没有任何网关时，这一行自动成为当前启用。"""
    name, submit_url, query_url = _validate_gateway_fields(
        name=name,
        submit_url=submit_url,
        append_submit_php=append_submit_php,
        merchant_id=merchant_id,
        query_url=query_url,
    )
    secret_key = (secret_key or "").strip()
    if not secret_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="商户密钥不能为空"
        )

    has_any = bool((await db.execute(select(func.count(PaymentGateway.id)))).scalar())
    gateway = PaymentGateway(
        name=name,
        submit_url=submit_url,
        append_submit_php=append_submit_php,
        merchant_id=merchant_id,
        secret_key=secret_key,
        query_url=query_url,
        alipay_enabled=alipay_enabled,
        wxpay_enabled=wxpay_enabled,
        enabled=enabled,
        is_active=not has_any and enabled,
    )
    db.add(gateway)
    await db.flush()
    await db.refresh(gateway)

    logger.info(
        "Payment gateway created: id=%s name=%s append_php=%s active=%s",
        gateway.id,
        name,
        append_submit_php,
        gateway.is_active,
    )
    return gateway


async def update_gateway(
    db: AsyncSession,
    gateway_id: int,
    *,
    name: str,
    submit_url: str,
    append_submit_php: bool,
    merchant_id: str,
    secret_key: str | None = None,
    query_url: str | None = None,
    alipay_enabled: bool = True,
    wxpay_enabled: bool = True,
    enabled: bool = True,
) -> PaymentGateway:
    """修改网关。``secret_key`` 为空表示保持原密钥不变。"""
    gateway = await get_gateway(db, gateway_id)
    name, submit_url, query_url = _validate_gateway_fields(
        name=name,
        submit_url=submit_url,
        append_submit_php=append_submit_php,
        merchant_id=merchant_id,
        query_url=query_url,
    )
    if gateway.is_active and not enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="当前正在使用的网关不能停用，请先切换到另一家",
        )

    gateway.name = name
    gateway.submit_url = submit_url
    gateway.append_submit_php = append_submit_php
    gateway.merchant_id = merchant_id
    gateway.query_url = query_url
    gateway.alipay_enabled = alipay_enabled
    gateway.wxpay_enabled = wxpay_enabled
    gateway.enabled = enabled
    if secret_key:
        gateway.secret_key = secret_key.strip()

    await db.flush()
    await db.refresh(gateway)
    return gateway


async def activate_gateway(db: AsyncSession, gateway_id: int) -> PaymentGateway:
    """把某个网关设为当前启用；停用或缺凭据的网关不允许选中。"""
    gateway = await get_gateway(db, gateway_id)
    if not gateway.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该网关已停用，不能设为当前使用",
        )
    if not gateway.is_configured:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该网关配置不完整（地址、商户号、密钥、支付方式），不能设为当前使用",
        )

    await _clear_active(db, except_id=gateway.id)
    gateway.is_active = True
    await db.flush()
    await db.refresh(gateway)

    logger.info("Payment gateway activated: id=%s name=%s", gateway.id, gateway.name)
    return gateway


async def delete_gateway(db: AsyncSession, gateway_id: int) -> None:
    """删除网关。当前启用或已被订单引用的不允许删，只能停用。"""
    gateway = await get_gateway(db, gateway_id)
    if gateway.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="当前正在使用的网关不能删除，请先切换到另一家",
        )
    referenced = (
        await db.execute(
            select(func.count(RechargeOrder.id)).where(
                RechargeOrder.gateway_id == gateway.id
            )
        )
    ).scalar()
    if referenced:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"该网关已有 {int(referenced)} 笔充值记录引用，不能删除；如需停用请关闭「启用」",
        )

    await db.delete(gateway)
    await db.flush()
    logger.info("Payment gateway deleted: id=%s name=%s", gateway_id, gateway.name)
