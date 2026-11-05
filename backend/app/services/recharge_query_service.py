"""充值订单主动查询 / 补单。

易支付的入账依赖上游异步回调（``recharge_service.handle_notify``）。现实中
回调会丢：用户付完款直接关页面、上游重试全部失败、或者回调打到了旧域名。
钱已经从用户账上扣走，本地订单却还停在 ``PENDING``，最后变成客服工单。

本模块按「商户单号」反查上游订单状态，把漏掉的到账补回来：

- ``query_gateway_order``：只读查询，拿到上游结论（已付 / 未付 / 查不到 …）；
- ``sync_recharge_order``：在查询结论为「已付且金额一致」时才触发入账；
- ``sync_pending_recharges``：批量扫描超时未支付的订单，供后台定时任务调用。

三条硬规则：

1. **补单必须核对金额。** 查询接口只告诉我们「这笔单付了多少钱」，如果
   上游说付了 0.01 元而订单是 1000 元，照着入账等于凭空送钱。金额不一致
   一律不碰状态、不动余额，留给人工处理。
2. **``status=0`` 不改状态。** 上游「未支付」不是失败，用户可能过几分钟
   才付款（扫码后回到页面再付很常见）。主动查询只做「已支付」这单向补录，
   绝不把订单改成失败/关闭，否则用户付了钱却已经无法入账。
3. **入账本身不重试。** 查询是只读的，网络抖动可以重试；``complete_recharge``
   会动余额，重试只交给它自身的行锁 + 唯一键幂等，这里不叠加循环。

商户密钥只出现在发往上游的查询参数里，任何日志都不打印密钥或带 key 的完整
查询 URL，定位订单一律用商户单号。
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment_gateway import PaymentGateway
from app.models.recharge import RechargeOrder, RechargeStatus
from app.services.payment_gateway_service import resolve_query_url
from app.services.wallet_service import get_wallet_service

logger = logging.getLogger(__name__)

__all__ = [
    "QueryOutcome",
    "QueryResult",
    "query_gateway_order",
    "sync_pending_recharges",
    "sync_recharge_order",
]

_CENT = Decimal("0.01")

# 连接 5s：查询接口是「顺手查一下」，连不上就别把后台任务拖住；
# 读 15s：上游偶发慢查询，留出足够时间但不至于挂死整轮扫描。
CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 15.0

# 只读查询允许的最多重试次数（不含首次请求）。仅网络异常重试：
# HTTP 500 / 非 JSON 这类是上游明确给出的结果，重试也不会变好。
MAX_QUERY_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 0.5

# 上游 JSON 存进 ``notify_payload`` 前逐字段截断：留证据即可，不把异常
# 超长的响应体整段写库。
_PAYLOAD_FIELD_LIMIT = 200

# 主动补单写入 ``notify_payload`` 的来源前缀，与真回调原文区分开。
_SYNC_PAYLOAD_PREFIX = "主动查询补单（非上游回调）："


class QueryOutcome(str, Enum):
    """一次上游查询或补单尝试的结论。

    刻意把「查不到」和「出错」分开：``NOT_FOUND`` 说明上游明确没有这笔单
    （多半是用户根本没付款），``ERROR`` 说明我们没问出结果，下一轮可以再查。
    """

    PAID = "PAID"
    UNPAID = "UNPAID"
    NOT_FOUND = "NOT_FOUND"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    UNSUPPORTED = "UNSUPPORTED"
    ERROR = "ERROR"


@dataclass
class QueryResult:
    """查询结论 + 面向管理员的说明 + 上游原始 JSON（字段已截断）。"""

    outcome: QueryOutcome
    message: str
    upstream: dict | None = None


def _parse_money(value) -> Decimal | None:
    """把上游金额解析为两位小数 ``Decimal``；无法解析时返回 ``None``。

    上游有时回 ``"1000.00"``，有时回数字 ``1000.0``，统一走 ``str`` → ``Decimal``，
    绝不用 float 参与比较（``0.1 + 0.2`` 的二进制误差会让对账结论飘）。
    舍入方式与 ``recharge_service`` / ``wallet_service`` 一致（ROUND_HALF_UP）。
    """
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return None


def _as_int(value) -> int | None:
    """把 int / ``"1"`` / ``1.0`` 一类值归一成 int；失败返回 ``None``。

    上游实现的 ``code`` / ``status`` / ``pid`` 类型不统一（有 int 也有字符串），
    直接 ``== 1`` 会因为 ``"1" != 1`` 把已支付订单误判成未支付。
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _truncate_payload(payload: dict) -> dict:
    """把上游 JSON 收敛成可安全入库的扁平字典（值转字符串并截断）。

    Returns:
        新字典；用于展示与对账留证，不参与任何业务判断。
    """
    trimmed: dict[str, object] = {}
    for key, value in payload.items():
        if isinstance(value, (int, float, bool)) or value is None:
            trimmed[str(key)] = value
            continue
        text = str(value)
        if len(text) > _PAYLOAD_FIELD_LIMIT:
            text = f"{text[:_PAYLOAD_FIELD_LIMIT]}…"
        trimmed[str(key)] = text
    return trimmed


async def _fetch_upstream(
    query_url: str, params: dict[str, str]
) -> tuple[dict | None, str]:
    """向网关查询接口发一次 GET，返回 (JSON 字典, 错误说明)。

    网络异常（连接失败 / 超时）最多重试 ``MAX_QUERY_RETRIES`` 次；HTTP 非 200
    与响应不是 JSON 都直接返回错误——那是上游已经给出的答复，重试无意义。
    """
    timeout = httpx.Timeout(
        connect=CONNECT_TIMEOUT_SECONDS,
        read=READ_TIMEOUT_SECONDS,
        write=CONNECT_TIMEOUT_SECONDS,
        pool=CONNECT_TIMEOUT_SECONDS,
    )
    last_error = "上游查询失败"

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(MAX_QUERY_RETRIES + 1):
            try:
                response = await client.get(query_url, params=params)
            except httpx.TimeoutException as err:
                # 超时信息里不含查询参数，可以安全记录类型名
                last_error = f"上游查询超时（{type(err).__name__}）"
            except httpx.TransportError as err:
                last_error = f"上游查询网络异常（{type(err).__name__}）"
            else:
                if response.status_code != 200:
                    return None, f"上游查询返回 HTTP {response.status_code}"
                try:
                    payload = response.json()
                except ValueError:
                    return None, "上游查询返回的不是合法 JSON"
                if not isinstance(payload, dict):
                    return None, "上游查询返回的 JSON 结构异常"
                return payload, ""

            if attempt < MAX_QUERY_RETRIES:
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))

    return None, last_error


async def query_gateway_order(
    gateway: PaymentGateway,
    trade_no: str,
    *,
    expected_amount: Decimal | None = None,
) -> QueryResult:
    """按商户单号查询上游订单状态（只读，不动钱、不改本地状态）。

    Args:
        gateway: 下单时使用的网关（决定查询地址、商户号与密钥）。
        trade_no: 商户单号，即 ``RechargeOrder.trade_no``。
        expected_amount: 本地订单金额。给出时会与上游 ``money`` 比对，
            不一致直接判 ``AMOUNT_MISMATCH``；不给出时只判断「上游是否已收钱」。

    Returns:
        ``QueryResult``，``outcome`` 含义见 ``QueryOutcome``。

    金额核对放在这里而不是交给调用方，是因为它决定「这笔钱能不能入账」：
    上游只回「付了 X 元」，X 与订单金额不等时（改价、拆单、上游串单）绝不
    能按订单金额入账，必须停下来让人看。
    """
    query_url = resolve_query_url(gateway)
    if not query_url:
        # 不是每家网关都提供查询接口；显式说明，避免后台反复重试一个不存在的能力
        return QueryResult(
            QueryOutcome.UNSUPPORTED,
            "该网关未配置查询地址，暂不支持自动补单",
        )

    merchant_id = (gateway.merchant_id or "").strip()
    secret_key = (gateway.secret_key or "").strip()
    if not merchant_id or not secret_key:
        return QueryResult(
            QueryOutcome.ERROR,
            "网关缺少商户号或商户密钥，无法发起查询",
        )

    params = {
        "act": "order",
        "pid": merchant_id,
        "key": secret_key,
        "out_trade_no": trade_no,
    }
    payload, error = await _fetch_upstream(query_url, params)
    if payload is None:
        logger.warning(
            "Recharge query failed: trade_no=%s gateway=%s reason=%s",
            trade_no,
            gateway.id,
            error,
        )
        return QueryResult(QueryOutcome.ERROR, error)

    upstream = _truncate_payload(payload)

    # 注意：查不到时 HTTP 同样是 200，只有 code != 1 才能判定「没这笔单」
    if _as_int(payload.get("code")) != 1:
        message = str(payload.get("msg") or "").strip() or "上游未查询到该订单"
        return QueryResult(QueryOutcome.NOT_FOUND, message, upstream)

    status = _as_int(payload.get("status"))
    if status == 0:
        # 未支付：用户可能只是还没扫码/还没输密码。订单保持 PENDING，
        # 下一轮（或用户付款后的回调）还会再查，绝不能在这里改状态。
        return QueryResult(
            QueryOutcome.UNPAID,
            "上游显示买家尚未支付，订单保持待支付",
            upstream,
        )
    if status != 1:
        return QueryResult(
            QueryOutcome.ERROR,
            f"上游返回了无法识别的订单状态：{payload.get('status')!r}",
            upstream,
        )

    if expected_amount is not None:
        expected = _parse_money(expected_amount) or Decimal("0.00")
        paid = _parse_money(payload.get("money"))
        if paid is None:
            return QueryResult(
                QueryOutcome.AMOUNT_MISMATCH,
                f"上游已支付，但返回的金额无法解析（money={payload.get('money')!r}），需人工核对",
                upstream,
            )
        if paid != expected:
            logger.warning(
                "Recharge query amount mismatch: trade_no=%s expected=%s upstream=%s",
                trade_no,
                expected,
                paid,
            )
            return QueryResult(
                QueryOutcome.AMOUNT_MISMATCH,
                f"上游已支付 ¥{paid:.2f}，与订单金额 ¥{expected:.2f} 不一致，未入账，请人工核对",
                upstream,
            )

    return QueryResult(
        QueryOutcome.PAID,
        f"上游已支付 ¥{_parse_money(payload.get('money')) or Decimal('0.00'):.2f}",
        upstream,
    )


async def _load_gateway(
    db: AsyncSession, gateway_id: int | None
) -> PaymentGateway | None:
    """按 id 取网关；``gateway_id`` 为空（迁移前历史订单）时返回 ``None``。"""
    if gateway_id is None:
        return None
    result = await db.execute(
        select(PaymentGateway).where(PaymentGateway.id == gateway_id)
    )
    return result.scalar_one_or_none()


def _upstream_trade_no(upstream: dict | None) -> str | None:
    """上游单号（用于回填 ``epay_trade_no``）；缺失时返回 ``None``。"""
    value = str((upstream or {}).get("trade_no") or "").strip()
    return value or None


async def sync_recharge_order(
    db: AsyncSession,
    order: RechargeOrder,
) -> QueryResult:
    """查询单笔订单并按结论补单（只有「已付且金额一致」才会入账）。

    - 非 ``PENDING`` 订单不做任何事：已成功/已关闭/已失败都是终态，
      再查一次也不会改变结论，交给后台人工改判；
    - 上游查不到 / 未支付 / 金额不一致 / 查询出错：只返回结论，**不动余额、
      不改状态**（未支付不是失败，金额不一致必须人工介入）；
    - 已支付：调用 ``WalletService.complete_recharge`` 入账。该方法的行锁 +
      唯一键天然幂等，因此与易支付回调并发时也不会重复加钱。

    ``notify_payload`` 会写入带前缀的上游 JSON，明确标记这笔到账来自主动
    查询而非回调，事后对账能一眼看出差异。
    """
    if order.status != RechargeStatus.PENDING:
        return QueryResult(
            QueryOutcome.UNSUPPORTED,
            f"订单状态为 {order.status.value}，无需补单",
        )

    gateway = await _load_gateway(db, order.gateway_id)
    if gateway is None:
        # 迁移前的历史订单没有 gateway_id，网关被删除也会置 NULL：
        # 不知道商户号与密钥就无从查询，只能留给人工处理。
        return QueryResult(
            QueryOutcome.UNSUPPORTED,
            "订单未关联支付网关（历史订单或网关已删除），无法查询上游状态",
        )

    result = await query_gateway_order(
        gateway, order.trade_no, expected_amount=order.amount
    )
    if result.outcome is not QueryOutcome.PAID:
        return result

    payload_text = json.dumps(result.upstream or {}, ensure_ascii=False)
    wallet_service = get_wallet_service(db)
    completed = await wallet_service.complete_recharge(
        order.trade_no,
        epay_trade_no=_upstream_trade_no(result.upstream),
        notify_payload=f"{_SYNC_PAYLOAD_PREFIX}{payload_text}",
    )
    if completed is None:
        # 查询到已支付但没能入账：多半是同一时刻回调已经入账，或状态被人工改判。
        # complete_recharge 已经保证幂等，这里只报告，不重试。
        return QueryResult(
            QueryOutcome.ERROR,
            "上游已支付，但入账未生效（订单状态已变更或已入账），请核对",
            result.upstream,
        )

    order.status = completed.status
    order.epay_trade_no = completed.epay_trade_no
    logger.info(
        "Recharge order recovered by query: trade_no=%s amount=%s upstream_trade_no=%s",
        order.trade_no,
        order.amount,
        completed.epay_trade_no,
    )
    return QueryResult(
        QueryOutcome.PAID,
        f"补单成功：上游已支付，已入账 ¥{Decimal(str(order.amount)).quantize(_CENT, rounding=ROUND_HALF_UP):.2f}",
        result.upstream,
    )


_OUTCOME_COUNTER_KEYS = {
    QueryOutcome.PAID: "paid",
    QueryOutcome.UNPAID: "unpaid",
    QueryOutcome.NOT_FOUND: "not_found",
    QueryOutcome.AMOUNT_MISMATCH: "mismatch",
    QueryOutcome.ERROR: "errors",
    QueryOutcome.UNSUPPORTED: "unsupported",
}


async def sync_pending_recharges(
    db: AsyncSession,
    *,
    older_than_minutes: int = 10,
    limit: int = 100,
) -> dict:
    """扫描超时未支付的充值订单并尝试补单。

    只挑 ``created_at`` 早于 ``older_than_minutes`` 的 ``PENDING`` 订单：
    刚下单的订单用户很可能正在付款，查了也只会得到「未支付」，白白打上游。

    Args:
        db: 数据库会话；成功入账的改动由调用方统一 commit。
        older_than_minutes: 只处理创建时间早于「现在 - N 分钟」的订单。
        limit: 单轮最多处理多少条（控制对上游的压力）。

    Returns:
        ``{"scanned", "paid", "unpaid", "not_found", "mismatch", "errors",
        "unsupported", "skipped", "skipped_reasons"}``。
        ``skipped`` 是「没法查」的订单数（无网关 / 网关已停用），
        ``skipped_reasons`` 按原因分桶，便于后台看清是配置缺失还是网关停用。

    取舍：
    - **单条失败不影响其它**：某家网关打挂了不该拖住其它网关的补单，
      因此逐条 try/except，异常计入 ``errors``。
    - **停用的网关不查**：停用意味着该通道已下线，对它的查询既无意义也
      可能触发上游风控；这类订单计入 ``skipped``（配置复原后还能补）。
    - 只有 ``PAID`` 会真正入账；其余结论一律只统计。
    """
    minutes = max(0, int(older_than_minutes))
    max_rows = max(1, min(int(limit), 500))

    # created_at 由数据库写入（UTC），这里同样用 naive UTC 计算边界，
    # 与 dashboard_service 等处的口径一致。
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        minutes=minutes
    )

    result = await db.execute(
        select(RechargeOrder)
        .where(
            RechargeOrder.status == RechargeStatus.PENDING,
            RechargeOrder.created_at <= cutoff,
        )
        .order_by(RechargeOrder.id.asc())
        .limit(max_rows)
    )
    orders = list(result.scalars().all())

    summary = {
        "scanned": len(orders),
        "paid": 0,
        "unpaid": 0,
        "not_found": 0,
        "mismatch": 0,
        "errors": 0,
        "unsupported": 0,
        "skipped": 0,
        "skipped_reasons": {},
    }

    # 同一轮里同网关的订单很多，网关配置没必要每条都查一次
    gateway_cache: dict[int | None, PaymentGateway | None] = {}

    def _skip(reason: str) -> None:
        summary["skipped"] += 1
        reasons = summary["skipped_reasons"]
        reasons[reason] = reasons.get(reason, 0) + 1

    for order in orders:
        try:
            gateway_id = order.gateway_id
            if gateway_id not in gateway_cache:
                gateway_cache[gateway_id] = await _load_gateway(db, gateway_id)

            gateway = gateway_cache[gateway_id]
            if gateway is None:
                logger.info(
                    "Skip recharge sync (no gateway): trade_no=%s gateway_id=%s",
                    order.trade_no,
                    gateway_id,
                )
                _skip("gateway_missing")
                continue
            if not gateway.enabled:
                logger.info(
                    "Skip recharge sync (gateway disabled): trade_no=%s gateway=%s",
                    order.trade_no,
                    gateway.id,
                )
                _skip("gateway_disabled")
                continue

            query_result = await sync_recharge_order(db, order)
        except Exception:
            # 单条异常（网关查询 / 上游 / DB 抖动）不得中断整轮扫描
            logger.exception(
                "Recharge sync failed for trade_no=%s", order.trade_no
            )
            summary["errors"] += 1
            continue

        counter = _OUTCOME_COUNTER_KEYS.get(query_result.outcome)
        if counter is not None:
            summary[counter] += 1

    if summary["scanned"]:
        logger.info(
            "Recharge sync scanned=%s paid=%s unpaid=%s not_found=%s "
            "mismatch=%s errors=%s unsupported=%s skipped=%s reasons=%s",
            summary["scanned"],
            summary["paid"],
            summary["unpaid"],
            summary["not_found"],
            summary["mismatch"],
            summary["errors"],
            summary["unsupported"],
            summary["skipped"],
            summary["skipped_reasons"],
        )
    return summary
