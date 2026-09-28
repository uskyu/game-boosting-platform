"""提现打款批次测试：渠道开关 → 建批次 → 导出 .xls → 导入渠道回执。

覆盖：
- 后台提现渠道开关（支付宝/微信）与它对新增提现、/withdrawals/channels 的影响；
- 打款批次的创建校验（状态/渠道/重复加入/单批上限/分类开关）；
- 批量转账文件导出的模板结构（用 xlrd 反向解析导出的字节流）；
- 回执导入（.xls / .csv，UTF-8-BOM / GBK）：成功标记已打款、失败驳回退回、
  幂等重导、匹配规则、payout_result、批次关账快照。

所有时间断言都由「当前产品时区」现算（产品时区固定 +08:00，库内是 UTC），
不依赖用例运行时的墙钟时刻。
"""

import csv
import io
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import quote

import xlrd
import xlwt
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.product_time import product_local_date
from app.models.notification import Notification
from app.services.withdrawal_payout_service import MAX_BATCH_ITEMS
from tests.conftest import auth_header

# 支付宝/微信渠道回执的明细表头（与客户真实结果文件一致）。
RECEIPT_HEADERS = [
    "序号",
    "创建时间",
    "订单号",
    "流水号",
    "收款账号",
    "姓名",
    "金额（元）",
    "状态",
    "备注",
    "失败原因",
]

# 导出模板第 2 行（0-indexed 1）必须原样出现的五个表头。
EXPORT_HEADERS = [
    "序号（必填）",
    "收款方支付宝账号（必填）",
    "收款方姓名（必填）",
    "金额（必填，单位：元）",
    "备注（选填）",
]

ALIPAY_SHEET_NAME = "页一支付宝批量付款文件上传模板"


# =============================================================================
# 助手
# =============================================================================


def _default_remark_today() -> str:
    """建批次时默认备注按产品时区今天生成：M月D号提现打款。"""
    today = product_local_date(datetime.now(timezone.utc))
    return f"{today.month}月{today.day}号提现打款"


async def _register_user(
    client: AsyncClient, make_captcha, email: str, username: str
) -> dict:
    resp = await client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "password": "TestPass123",
            **make_captcha(),
        },
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _fund_wallet(
    client: AsyncClient, admin_user: dict, user_id: int, amount: str
) -> None:
    resp = await client.post(
        f"/admin/wallets/{user_id}/adjust",
        json={"amount": amount, "reason": "测试入账"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text


async def _apply_for_withdrawal(
    client: AsyncClient,
    admin_user: dict,
    make_captcha,
    *,
    email: str,
    username: str,
    amount: int,
    channel: str = "ALIPAY",
    account_name: str | None = None,
    account_no: str | None = None,
):
    """注册新用户 + 入账 + 提交提现申请，返回 (登录态, httpx 响应)。

    调用方自行断言状态码（渠道关闭时预期 400）。
    """
    user = await _register_user(client, make_captcha, email, username)
    user_id = user["user"]["id"]
    await _fund_wallet(client, admin_user, user_id, f"{amount}.00")
    resp = await client.post(
        "/withdrawals",
        json={
            "amount": amount,
            "channel": channel,
            "account_name": account_name or username,
            "account_no": account_no or f"acct{user_id}@payout.test",
        },
        headers=auth_header(user),
    )
    return user, resp


async def _make_withdrawal(
    client: AsyncClient,
    admin_user: dict,
    make_captcha,
    *,
    email: str,
    username: str,
    amount: int,
    channel: str = "ALIPAY",
    account_name: str | None = None,
    account_no: str | None = None,
) -> tuple[dict, dict]:
    """注册一个新用户、入账、申请提现，返回 (登录态, 提现 json)。

    每个用户只申请一笔：默认刷新规则下同一用户 24 小时内只有一次机会。
    """
    user, resp = await _apply_for_withdrawal(
        client,
        admin_user,
        make_captcha,
        email=email,
        username=username,
        amount=amount,
        channel=channel,
        account_name=account_name,
        account_no=account_no,
    )
    assert resp.status_code == 201, resp.text
    return user, resp.json()


async def _approve(client: AsyncClient, admin_user: dict, withdrawal_id: int) -> dict:
    resp = await client.post(
        f"/admin/withdrawals/{withdrawal_id}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _reject(client: AsyncClient, admin_user: dict, withdrawal_id: int) -> dict:
    resp = await client.post(
        f"/admin/withdrawals/{withdrawal_id}/review",
        json={"action": "reject", "reason": "账号信息有误"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _mark_paid(
    client: AsyncClient, admin_user: dict, withdrawal_id: int, reference: str
) -> dict:
    resp = await client.post(
        f"/admin/withdrawals/{withdrawal_id}/mark-paid",
        json={"payment_reference": reference},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _create_batch(
    client: AsyncClient,
    admin_user: dict,
    *,
    ids: list[int],
    category: str = "ALIPAY",
    remark: str | None = None,
):
    payload: dict = {"ids": ids, "category": category}
    if remark is not None:
        payload["remark"] = remark
    return await client.post(
        "/admin/withdrawals/payout-batches",
        json=payload,
        headers=auth_header(admin_user),
    )


async def _wallet_of(client: AsyncClient, user_data: dict) -> dict:
    resp = await client.get("/wallet", headers=auth_header(user_data))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _ledger_of(
    client: AsyncClient, user_data: dict, tx_type: str
) -> list[dict]:
    resp = await client.get("/wallet/transactions", headers=auth_header(user_data))
    assert resp.status_code == 200, resp.text
    return [tx for tx in resp.json()["items"] if tx["type"] == tx_type]


def _export_detail_rows(sheet) -> list[list]:
    """取出导出文件里第 3 行起的明细（A 列「序号」非空的行，每行前 5 列）。"""
    rows: list[list] = []
    for row_index in range(2, sheet.nrows):
        seq = sheet.cell_value(row_index, 0)
        if seq == "" or seq is None:
            continue
        rows.append([sheet.cell_value(row_index, col) for col in range(5)])
    return rows


def _receipt_row(
    *,
    account_no: str,
    account_name: str,
    amount,
    status: str = "成功",
    seq="",
    order_no: str = "",
    serial_no: str = "",
    remark: str = "",
    fail_reason: str = "",
    created_time: str = "2026-01-01 10:00:00",
) -> dict:
    return {
        "seq": seq,
        "created_time": created_time,
        "order_no": order_no,
        "serial_no": serial_no,
        "account_no": account_no,
        "account_name": account_name,
        "amount": amount,
        "status": status,
        "remark": remark,
        "fail_reason": fail_reason,
    }


def _receipt_detail_cells(row: dict) -> list:
    """明细行 → 与 RECEIPT_HEADERS 对齐的单元格列表。"""
    return [
        row["seq"],
        row["created_time"],
        row["order_no"],
        row["serial_no"],
        row["account_no"],
        row["account_name"],
        row["amount"],
        row["status"],
        row["remark"],
        row["fail_reason"],
    ]


def _receipt_header_cells(
    batch_order_no: str,
    *,
    total: str = "999.99",
    success: str = "888.88",
    fail: str = "1.11",
) -> list:
    """抬头区：账号户称/付款账户/订单号/付款状态/总金额（元）/成功金额（元）/失败金额（元）。

    汇总金额故意写错，用来验证关账快照取「实际处理行」而不是回执自报汇总。
    """
    return [
        "账号户称",
        "某某网络科技有限公司",
        "付款账户",
        "pay@alipay.com",
        "订单号",
        batch_order_no,
        "付款状态",
        "全部成功",
        "总金额（元）",
        total,
        "成功金额（元）",
        success,
        "失败金额（元）",
        fail,
    ]


def build_receipt_xls(rows: list[dict], batch_order_no: str) -> bytes:
    """用 xlwt 伪造一份渠道批量付款结果文件（.xls）。

    布局对齐客户真实回执：抬头区一行 → 空行 → 明细表头 → 明细行。
    """
    workbook = xlwt.Workbook(encoding="utf-8")
    sheet = workbook.add_sheet("批量付款回单")
    for col, value in enumerate(_receipt_header_cells(batch_order_no)):
        sheet.write(0, col, value)
    sheet.write(1, 0, "")  # 空行
    for col, header in enumerate(RECEIPT_HEADERS):
        sheet.write(2, col, header)
    for offset, row in enumerate(rows):
        for col, value in enumerate(_receipt_detail_cells(row)):
            sheet.write(3 + offset, col, value)
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def build_receipt_csv(
    rows: list[dict], batch_order_no: str, *, encoding: str = "utf-8-sig"
) -> bytes:
    """同样的明细表另存为 CSV（支付宝 CSV 无严格版式要求）。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_receipt_header_cells(batch_order_no))
    writer.writerow([""])
    writer.writerow(RECEIPT_HEADERS)
    for row in rows:
        writer.writerow(_receipt_detail_cells(row))
    return buffer.getvalue().encode(encoding)


async def _import_receipt(
    client: AsyncClient,
    admin_user: dict,
    batch_id: int,
    *,
    filename: str,
    content: bytes,
):
    return await client.post(
        f"/admin/withdrawals/payout-batches/{batch_id}/import-receipt",
        files={"file": (filename, content, "application/vnd.ms-excel")},
        headers=auth_header(admin_user),
    )


async def _admin_withdrawal(
    client: AsyncClient, admin_user: dict, withdrawal_id: int
) -> dict:
    """从后台提现列表里取回单条提现（含 reviewed_by/paid_by 等审计字段）。"""
    resp = await client.get("/admin/withdrawals", headers=auth_header(admin_user))
    assert resp.status_code == 200, resp.text
    for item in resp.json()["items"]:
        if item["id"] == withdrawal_id:
            return item
    raise AssertionError(f"提现 #{withdrawal_id} 不在后台列表中")


async def _batch_detail(
    client: AsyncClient, admin_user: dict, batch_id: int
) -> dict:
    resp = await client.get(
        f"/admin/withdrawals/payout-batches/{batch_id}",
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# =============================================================================
# 1/2/5. 渠道开关：默认值、局部更新、校验、权限
# =============================================================================


async def test_channel_settings_defaults_and_partial_update(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    """默认两个渠道都开放；PUT 只改一个字段不影响另一个；空 body / 未知字段 422。"""
    # a. 全新库默认值
    resp = await client.get(
        "/admin/withdrawal-category/settings", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["alipay_enabled"] is True, "默认应开放支付宝"
    assert data["wechat_enabled"] is True, "默认应开放微信"

    # b. 只改一个字段：另一个保持原值
    resp = await client.put(
        "/admin/withdrawal-category/settings",
        json={"wechat_enabled": False},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["wechat_enabled"] is False
    assert data["alipay_enabled"] is True, "未传的字段不应被改动"

    resp = await client.get(
        "/admin/withdrawal-category/settings", headers=auth_header(admin_user)
    )
    assert resp.json()["alipay_enabled"] is True
    assert resp.json()["wechat_enabled"] is False

    # 再只改支付宝，微信保持关闭
    resp = await client.put(
        "/admin/withdrawal-category/settings",
        json={"alipay_enabled": False},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["alipay_enabled"] is False
    assert data["wechat_enabled"] is False

    # 两个一起打开
    resp = await client.put(
        "/admin/withdrawal-category/settings",
        json={"alipay_enabled": True, "wechat_enabled": True},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["alipay_enabled"] is True
    assert data["wechat_enabled"] is True
    assert data["updated_by"] == admin_user["user"]["id"]
    assert data["updated_at"] is not None

    # c. 空 body：一个开关都没改 → 422
    resp = await client.put(
        "/admin/withdrawal-category/settings",
        json={},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 422, resp.text

    # d. 未知字段 → extra=forbid → 422
    resp = await client.put(
        "/admin/withdrawal-category/settings",
        json={"alipay_enabled": True, "bank_enabled": True},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 422, resp.text

    # e. 普通用户读写都 403
    for method in ("get", "put"):
        if method == "get":
            resp = await client.get(
                "/admin/withdrawal-category/settings",
                headers=auth_header(registered_user),
            )
        else:
            resp = await client.put(
                "/admin/withdrawal-category/settings",
                json={"wechat_enabled": True},
                headers=auth_header(registered_user),
            )
        assert resp.status_code == 403, f"{method} 非管理员应 403：{resp.text}"


# =============================================================================
# 3. 关闭微信：新增被拦、旧提现照样可审
# =============================================================================


async def test_disabled_wechat_blocks_new_withdrawals_only(
    client: AsyncClient,
    admin_user: dict,
    make_captcha,
):
    """关闭微信提现后：新微信申请 400，支付宝/银行卡照常；已存在的微信提现仍可审核。"""
    # 先制造两笔微信提现（关闭前申请）
    old_a, wid_a = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="old_wechat_a@example.com",
        username="OldWechatA",
        amount=20,
        channel="WECHAT",
    )
    old_b, wid_b = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="old_wechat_b@example.com",
        username="OldWechatB",
        amount=25,
        channel="WECHAT",
    )
    assert wid_a["status"] == "PENDING"
    assert wid_b["status"] == "PENDING"

    # 关闭微信
    resp = await client.put(
        "/admin/withdrawal-category/settings",
        json={"wechat_enabled": False},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text

    # 新微信申请被拦（400 + 中文提示）
    _, blocked = await _apply_for_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="new_wechat@example.com",
        username="NewWechat",
        amount=30,
        channel="WECHAT",
    )
    assert blocked.status_code == 400, blocked.text
    assert "微信提现暂未开放" in blocked.json()["detail"], blocked.text

    # 支付宝 / 银行卡不受影响
    _, alipay = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="new_alipay@example.com",
        username="NewAlipay",
        amount=31,
        channel="ALIPAY",
    )
    assert alipay["status"] == "PENDING"
    _, bank = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="new_bank@example.com",
        username="NewBank",
        amount=32,
        channel="BANK",
    )
    assert bank["status"] == "PENDING"

    # 关闭前申请的微信提现：审核通过 / 驳回都仍然可用
    approved = await _approve(client, admin_user, wid_a["id"])
    assert approved["status"] == "APPROVED"
    rejected = await _reject(client, admin_user, wid_b["id"])
    assert rejected["status"] == "REJECTED"

    # 旧微信用户的资金未受影响（驳回那笔已解冻）
    wallet_b = await _wallet_of(client, old_b)
    assert Decimal(str(wallet_b["available_balance"])) == Decimal("25.00")
    assert Decimal(str(wallet_b["frozen_balance"])) == Decimal("0.00")


# =============================================================================
# 4. /withdrawals/channels 的开放标记
# =============================================================================


async def test_withdrawal_channels_endpoint_flags(
    client: AsyncClient, registered_user: dict, admin_user: dict
):
    """普通用户读渠道选项：标记随后台开关变化，BANK 恒为开放。"""
    resp = await client.get("/withdrawals/channels", headers=auth_header(registered_user))
    assert resp.status_code == 200, resp.text
    options = {item["channel"]: item for item in resp.json()["items"]}
    assert set(options) == {"ALIPAY", "WECHAT", "BANK"}
    assert options["ALIPAY"]["enabled"] is True
    assert options["WECHAT"]["enabled"] is True
    assert options["BANK"]["enabled"] is True, "银行卡不受开关控制，始终开放"
    assert options["ALIPAY"]["label"] == "支付宝"

    resp = await client.put(
        "/admin/withdrawal-category/settings",
        json={"wechat_enabled": False},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200

    resp = await client.get("/withdrawals/channels", headers=auth_header(registered_user))
    options = {item["channel"]: item for item in resp.json()["items"]}
    assert options["ALIPAY"]["enabled"] is True
    assert options["WECHAT"]["enabled"] is False
    assert options["BANK"]["enabled"] is True


# =============================================================================
# 6. 建批次：成功路径
# =============================================================================


async def test_create_payout_batch_happy_path(
    client: AsyncClient, admin_user: dict, make_captcha
):
    """一笔待审核 + 一笔已审核通过的支付宝提现 → 批次 OPEN，笔数/总额/默认备注正确。"""
    pending_user, pending = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="batch_pending@example.com",
        username="BatchPending",
        amount=30,
    )
    approved_user, approved = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="batch_approved@example.com",
        username="BatchApproved",
        amount=70,
    )
    await _approve(client, admin_user, approved["id"])

    resp = await _create_batch(
        client, admin_user, ids=[pending["id"], approved["id"]]
    )
    assert resp.status_code == 201, resp.text
    batch = resp.json()
    assert batch["status"] == "OPEN"
    assert batch["category"] == "ALIPAY"
    assert batch["item_count"] == 2
    assert Decimal(str(batch["total_amount"])) == Decimal("100.00")
    assert batch["remark"] == _default_remark_today()
    assert batch["remark"], "默认备注不应为空"
    assert batch["created_by"] == admin_user["user"]["id"]
    assert batch["success_count"] is None, "未导回执前快照为空"
    assert batch["reference_no"] is None
    assert batch["exportable_count"] == 2, "两笔都还没导回执，均可导出"

    items = {item["id"]: item for item in batch["items"]}
    assert set(items) == {pending["id"], approved["id"]}
    assert items[pending["id"]]["status"] == "PENDING"
    assert items[approved["id"]]["status"] == "APPROVED"
    assert items[pending["id"]]["payout_result"] is None
    assert items[pending["id"]]["username"] == "BatchPending"
    assert items[pending["id"]]["account_no"] == pending["account_no"]

    # 建批次不动钱：两笔提现的冻结金额原样保留
    for user, withdrawal in ((pending_user, pending), (approved_user, approved)):
        wallet = await _wallet_of(client, user)
        assert Decimal(str(wallet["frozen_balance"])) == Decimal(
            str(withdrawal["amount"])
        )
        assert Decimal(str(wallet["total_withdrawn"])) == Decimal("0.00")
        assert await _ledger_of(client, user, "WITHDRAWAL_FREEZE") != []

    # 同一批提现不能再重复加入另一个 OPEN 批次
    resp = await _create_batch(
        client,
        admin_user,
        ids=[pending["id"], approved["id"]],
        remark="另一批",
    )
    assert resp.status_code == 400, resp.text
    assert "已属于批次" in resp.json()["detail"], resp.text

    # 自定义备注原样落库（换一笔提现再建）
    _, fresh = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="batch_fresh@example.com",
        username="BatchFresh",
        amount=80,
    )
    resp = await _create_batch(
        client, admin_user, ids=[fresh["id"]], remark="  6月1日提现打款  "
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["remark"] == "6月1日提现打款", "备注 strip 后落库"
    assert Decimal(str(resp.json()["total_amount"])) == Decimal("80.00")


# =============================================================================
# 7. 建批次校验
# =============================================================================


async def test_create_payout_batch_rejects_terminal_withdrawals(
    client: AsyncClient, admin_user: dict, make_captcha
):
    """已打款 / 已驳回的提现不能加入批次（整批拒绝，不产生半应用批次）。"""
    _, paid = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="term_paid@example.com",
        username="TermPaid",
        amount=40,
    )
    await _approve(client, admin_user, paid["id"])
    await _mark_paid(client, admin_user, paid["id"], "MANUAL-PAID-1")

    _, rejected = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="term_rejected@example.com",
        username="TermRejected",
        amount=45,
    )
    await _reject(client, admin_user, rejected["id"])

    resp = await _create_batch(client, admin_user, ids=[paid["id"]])
    assert resp.status_code == 400, resp.text
    assert "不能加入打款批次" in resp.json()["detail"], resp.text

    resp = await _create_batch(client, admin_user, ids=[rejected["id"]])
    assert resp.status_code == 400, resp.text
    assert "不能加入打款批次" in resp.json()["detail"], resp.text

    # 混入一条终态提现同样整批拒绝
    _, ok = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="term_ok@example.com",
        username="TermOk",
        amount=50,
    )
    resp = await _create_batch(client, admin_user, ids=[ok["id"], paid["id"]])
    assert resp.status_code == 400, resp.text
    assert "不能加入打款批次" in resp.json()["detail"], resp.text

    # 没有产生任何批次
    resp = await client.get(
        "/admin/withdrawals/payout-batches", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 0, "校验失败的请求不应留下批次"


async def test_create_payout_batch_rejects_wrong_category(
    client: AsyncClient, admin_user: dict, make_captcha
):
    """微信提现混进支付宝批次、支付宝提现混进微信批次 → 400；纯微信批次可以建。"""
    _, alipay = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="cat_alipay@example.com",
        username="CatAlipay",
        amount=60,
    )
    _, wechat = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="cat_wechat@example.com",
        username="CatWechat",
        amount=65,
        channel="WECHAT",
    )

    # 微信提现放进支付宝批次
    resp = await _create_batch(
        client, admin_user, ids=[alipay["id"], wechat["id"]], category="ALIPAY"
    )
    assert resp.status_code == 400, resp.text
    assert "不是支付宝渠道的提现" in resp.json()["detail"], resp.text

    # 支付宝提现放进微信批次
    resp = await _create_batch(
        client, admin_user, ids=[alipay["id"], wechat["id"]], category="WECHAT"
    )
    assert resp.status_code == 400, resp.text
    assert "不是微信渠道的提现" in resp.json()["detail"], resp.text

    # 只放微信提现 → 成功
    resp = await _create_batch(client, admin_user, ids=[wechat["id"]], category="WECHAT")
    assert resp.status_code == 201, resp.text
    assert resp.json()["category"] == "WECHAT"

    # 已经加入批次的提现不能重复加入（换个分类也不行）
    resp = await _create_batch(
        client, admin_user, ids=[wechat["id"]], category="WECHAT"
    )
    assert resp.status_code == 400, resp.text
    assert "已属于批次" in resp.json()["detail"], resp.text

    # 支付宝那笔仍然可以单独建批次
    resp = await _create_batch(client, admin_user, ids=[alipay["id"]], category="ALIPAY")
    assert resp.status_code == 201, resp.text


async def test_create_payout_batch_category_switch_off(
    client: AsyncClient, admin_user: dict, make_captcha
):
    """微信开关关闭后，微信分类不能再建打款批次。"""
    # 关闭前申请两笔微信提现
    _, w1 = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="switch_w1@example.com",
        username="SwitchW1",
        amount=20,
        channel="WECHAT",
    )
    _, w2 = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="switch_w2@example.com",
        username="SwitchW2",
        amount=21,
        channel="WECHAT",
    )

    resp = await client.put(
        "/admin/withdrawal-category/settings",
        json={"wechat_enabled": False},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text

    resp = await _create_batch(
        client, admin_user, ids=[w1["id"], w2["id"]], category="WECHAT"
    )
    assert resp.status_code == 400, resp.text
    assert "分类已关闭" in resp.json()["detail"], resp.text

    # 支付宝分类不受影响
    _, a1 = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="switch_a1@example.com",
        username="SwitchA1",
        amount=22,
        channel="ALIPAY",
    )
    resp = await _create_batch(client, admin_user, ids=[a1["id"]], category="ALIPAY")
    assert resp.status_code == 201, resp.text


async def test_create_payout_batch_rejects_empty_and_oversized_ids(
    client: AsyncClient, admin_user: dict, make_captcha
):
    """空 id 列表与超过单批上限的 id 数都被拒（schema 层即校验，实现层同样有兜底）。"""
    _, a1 = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="size_a1@example.com",
        username="SizeA1",
        amount=10,
    )

    # 空列表：schema min_length=1 先拦（422），service 的空列表守卫同样会 400
    resp = await _create_batch(client, admin_user, ids=[])
    assert resp.status_code in (400, 422), resp.text

    # 超过 MAX_BATCH_ITEMS：schema max_length 先拦（422），service 上限守卫同样会 400
    oversized = [10 ** 9 + index for index in range(MAX_BATCH_ITEMS + 1)]
    resp = await _create_batch(client, admin_user, ids=oversized)
    assert resp.status_code in (400, 422), resp.text
    assert "3000" in resp.text, resp.text

    # 恰好等于上限但 id 不存在 → 缺 id 的 400（证明走的是 service 校验）
    resp = await _create_batch(
        client, admin_user, ids=list(range(1, MAX_BATCH_ITEMS + 1))
    )
    assert resp.status_code == 400, resp.text
    assert "不存在" in resp.json()["detail"], resp.text

    # 正常的一笔仍然可以建批次
    resp = await _create_batch(client, admin_user, ids=[a1["id"]])
    assert resp.status_code == 201, resp.text


# =============================================================================
# 8. 后台提现列表的渠道筛选（回归：status/page 仍可用）
# =============================================================================


async def test_admin_withdrawal_list_channel_filter(
    client: AsyncClient, admin_user: dict, make_captcha
):
    """channel 筛选只返回对应渠道；不带参数返回全部；与 status 组合仍然生效。"""
    _, alipay = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="filter_alipay@example.com",
        username="FilterAlipay",
        amount=10,
        channel="ALIPAY",
    )
    _, wechat = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="filter_wechat@example.com",
        username="FilterWechat",
        amount=11,
        channel="WECHAT",
    )
    _, bank = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="filter_bank@example.com",
        username="FilterBank",
        amount=12,
        channel="BANK",
    )
    await _approve(client, admin_user, wechat["id"])

    resp = await client.get(
        "/admin/withdrawals?channel=ALIPAY", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 1
    assert [item["channel"] for item in data["items"]] == ["ALIPAY"]
    assert data["items"][0]["id"] == alipay["id"]

    resp = await client.get(
        "/admin/withdrawals?channel=WECHAT", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 1
    assert [item["channel"] for item in data["items"]] == ["WECHAT"]

    resp = await client.get("/admin/withdrawals", headers=auth_header(admin_user))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 3, "不带渠道参数应返回全部渠道"
    assert {item["channel"] for item in data["items"]} == {"ALIPAY", "WECHAT", "BANK"}

    # 回归：status + channel 组合筛选
    resp = await client.get(
        "/admin/withdrawals?channel=WECHAT&status=PENDING",
        headers=auth_header(admin_user),
    )
    assert resp.json()["total"] == 0, "那笔微信提现已审核通过"
    resp = await client.get(
        "/admin/withdrawals?channel=ALIPAY&status=PENDING",
        headers=auth_header(admin_user),
    )
    assert resp.json()["total"] == 1

    # 回归：分页参数仍然生效
    resp = await client.get(
        "/admin/withdrawals?page=2&page_size=2", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 3
    assert data["page"] == 2
    assert data["page_size"] == 2
    assert data["pages"] == 2
    assert len(data["items"]) == 1


# =============================================================================
# 9. 导出 .xls 模板结构
# =============================================================================


async def test_export_batch_xls_layout(
    client: AsyncClient, admin_user: dict, make_captcha
):
    """导出文件按支付宝模板布局生成，xlrd 能反向解析出表头与明细。"""
    _, first = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="exp_1@example.com",
        username="ExpOne",
        amount=30,
        account_name="张三",
        account_no="zhangsan@alipay.test",
    )
    _, second = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="exp_2@example.com",
        username="ExpTwo",
        amount=70,
        account_name="李四",
        account_no="lisi@alipay.test",
    )
    resp = await _create_batch(
        client, admin_user, ids=[first["id"], second["id"]], remark="6月1日提现打款"
    )
    assert resp.status_code == 201, resp.text
    batch_id = resp.json()["id"]

    resp = await client.get(
        f"/admin/withdrawals/payout-batches/{batch_id}/export.xls",
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    assert "application/vnd.ms-excel" in resp.headers["content-type"]
    disposition = resp.headers["content-disposition"]
    assert "attachment" in disposition, disposition
    assert ".xls" in disposition, disposition
    # 响应头必须 latin-1 可编码：中文备注（默认备注就是中文）不能把导出搞炸
    disposition.encode("latin-1")
    assert quote("6月1日提现打款") in disposition, disposition

    book = xlrd.open_workbook(file_contents=resp.content)
    assert book.sheet_names() == [ALIPAY_SHEET_NAME], book.sheet_names()
    sheet = book.sheet_by_index(0)
    assert sheet.name == ALIPAY_SHEET_NAME

    # 第 2 行（0-indexed 1）是表头：前五列就是五个必填/选填列
    header = [sheet.cell_value(1, col) for col in range(5)]
    assert header == EXPORT_HEADERS, header

    # 明细从第 3 行（0-indexed 2）开始，按提现 id 升序
    details = _export_detail_rows(sheet)
    assert len(details) == 2, f"应有两笔明细：{details}"
    first_cells, second_cells = details
    assert first_cells[0] == first["id"], "序号必须等于提现 id"
    assert first_cells[1] == "zhangsan@alipay.test"
    assert first_cells[2] == "张三"
    assert first_cells[3] == "30.00", "金额写成两位小数字符串"
    assert first_cells[4] == f"GW提现单W{first['id']}"
    assert second_cells[0] == second["id"]
    assert second_cells[3] == "70.00"
    assert second_cells[4] == f"GW提现单W{second['id']}"

    # 抬头说明写在 H 列（index 7）起，与模板一致
    assert sheet.cell_value(0, 7).startswith("填写说明")


# =============================================================================
# 10/11. 导出跳过建批次后被驳回的提现；空批次与权限
# =============================================================================


async def test_export_skips_withdrawals_rejected_after_creation(
    client: AsyncClient, admin_user: dict, make_captcha
):
    """建批次后提现再被驳回 → 导出时静默跳过，明细行消失。"""
    _, keep = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="exp_keep@example.com",
        username="ExpKeep",
        amount=30,
    )
    _, dropped = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="exp_drop@example.com",
        username="ExpDrop",
        amount=40,
    )
    resp = await _create_batch(client, admin_user, ids=[keep["id"], dropped["id"]])
    assert resp.status_code == 201, resp.text
    batch_id = resp.json()["id"]

    # 先能导出两行
    resp = await client.get(
        f"/admin/withdrawals/payout-batches/{batch_id}/export.xls",
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    sheet = xlrd.open_workbook(file_contents=resp.content).sheet_by_index(0)
    assert len(_export_detail_rows(sheet)) == 2

    await _reject(client, admin_user, dropped["id"])

    resp = await client.get(
        f"/admin/withdrawals/payout-batches/{batch_id}/export.xls",
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    sheet = xlrd.open_workbook(file_contents=resp.content).sheet_by_index(0)
    details = _export_detail_rows(sheet)
    assert len(details) == 1, "被驳回的那笔不再出现在导出文件里"
    assert details[0][0] == keep["id"]
    assert details[0][4] == f"GW提现单W{keep['id']}"

    # 明细里仍看得到它（批次成员关系不变），但可导出笔数减一
    detail = await _batch_detail(client, admin_user, batch_id)
    assert len(detail["items"]) == 2
    assert detail["exportable_count"] == 1

    # 剩下那笔也驳回 → 没有可导出项 → 400
    await _reject(client, admin_user, keep["id"])
    resp = await client.get(
        f"/admin/withdrawals/payout-batches/{batch_id}/export.xls",
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 400, resp.text
    assert "无可导出" in resp.json()["detail"], resp.text


async def test_export_batch_permissions_and_missing_batch(
    client: AsyncClient, admin_user: dict, registered_user: dict, make_captcha
):
    """导出：非管理员 403，批次不存在 404。"""
    _, a1 = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="exp_perm@example.com",
        username="ExpPerm",
        amount=30,
    )
    resp = await _create_batch(client, admin_user, ids=[a1["id"]])
    batch_id = resp.json()["id"]

    resp = await client.get(
        f"/admin/withdrawals/payout-batches/{batch_id}/export.xls",
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 403, resp.text

    resp = await client.get(
        "/admin/withdrawals/payout-batches/999999/export.xls",
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 404, resp.text

    resp = await client.get(
        "/admin/withdrawals/payout-batches/999999",
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 404, resp.text


# =============================================================================
# 12/13/14. 回执导入：成功打款 + 失败驳回 + 关账快照
# =============================================================================


async def test_import_receipt_mixed_success_and_failure(
    client: AsyncClient,
    admin_user: dict,
    make_captcha,
    db_session: AsyncSession,
):
    """混合回执：A 成功（待审核→打款）、B 失败（驳回退回+通知）、一行匹配不上。"""
    admin_id = admin_user["user"]["id"]
    user_a, a = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="mix_a@example.com",
        username="MixA",
        amount=30,
        account_name="张三",
        account_no="mix_a@alipay.test",
    )
    user_b, b = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="mix_b@example.com",
        username="MixB",
        amount=50,
        account_name="李四",
        account_no="mix_b@alipay.test",
    )
    assert a["status"] == "PENDING", "A 保持待审核，用来验证「回执即审批」"
    await _approve(client, admin_user, b["id"])

    resp = await _create_batch(client, admin_user, ids=[a["id"], b["id"]])
    assert resp.status_code == 201, resp.text
    batch_id = resp.json()["id"]

    order_no = "20260101990001"
    rows = [
        _receipt_row(
            account_no="mix_a@alipay.test",
            account_name="张三",
            amount=30,
            status="成功",
            seq="坏序号",
            order_no="ORD-MIX-A",
            serial_no="SER-MIX-A",
            remark=f"GW提现单W{a['id']}",
        ),
        _receipt_row(
            account_no="mix_b@alipay.test",
            account_name="李四",
            amount=50,
            status="失败",
            order_no="ORD-MIX-B",
            serial_no="",
            remark=f"GW提现单W{b['id']}",
            fail_reason="账户不存在",
        ),
        _receipt_row(
            account_no="ghost@alipay.test",
            account_name="幽灵用户",
            amount=12,
            status="成功",
            order_no="ORD-MIX-X",
            serial_no="SER-MIX-X",
        ),
    ]
    resp = await _import_receipt(
        client,
        admin_user,
        batch_id,
        filename="receipt.xls",
        content=build_receipt_xls(rows, order_no),
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["success"] == 1, result
    assert result["failed"] == 1, result
    assert result["unmatched"] == 1, result
    assert result["skipped"] == 0, result
    assert result["total_rows"] == 3
    assert result["batch_status"] == "CLOSED"
    assert result["reference_no"] == order_no
    assert result["receipt_header"]["order_no"] == order_no

    by_withdrawal = {
        item["withdrawal_id"]: item for item in result["items"]
    }
    assert by_withdrawal[a["id"]]["result"] == "PAID"
    assert by_withdrawal[a["id"]]["reason"] == "SER-MIX-A"
    assert by_withdrawal[b["id"]]["result"] == "REJECTED"
    assert by_withdrawal[b["id"]]["reason"].startswith("批量打款失败：")
    assert by_withdrawal[None]["result"] == "UNMATCHED"

    # ---- A：回执即审批 → 已打款 ----
    a_row = await _admin_withdrawal(client, admin_user, a["id"])
    assert a_row["status"] == "PAID", "待审核提现被回执直接带成已打款"
    assert a_row["payment_reference"] == "SER-MIX-A", "支付凭证取回执流水号"
    assert a_row["reviewed_by"] == admin_id
    assert a_row["reviewed_at"] is not None
    assert a_row["paid_by"] == admin_id
    assert a_row["paid_at"] is not None

    wallet_a = await _wallet_of(client, user_a)
    assert Decimal(str(wallet_a["frozen_balance"])) == Decimal("0.00"), "冻结被扣掉"
    assert Decimal(str(wallet_a["total_withdrawn"])) == Decimal("30.00")
    assert Decimal(str(wallet_a["available_balance"])) == Decimal("0.00"), "可用余额不变"

    paid_txs = await _ledger_of(client, user_a, "WITHDRAWAL_PAID")
    assert len(paid_txs) == 1, "只应有一条打款流水"
    assert Decimal(str(paid_txs[0]["amount"])) == Decimal("-30.00")
    assert paid_txs[0]["withdrawal_id"] == a["id"]

    # ---- B：失败 → 驳回退回 ----
    b_row = await _admin_withdrawal(client, admin_user, b["id"])
    assert b_row["status"] == "REJECTED"
    assert b_row["reject_reason"].startswith("批量打款失败："), b_row["reject_reason"]
    assert "账户不存在" in b_row["reject_reason"]

    wallet_b = await _wallet_of(client, user_b)
    assert Decimal(str(wallet_b["frozen_balance"])) == Decimal("0.00")
    assert Decimal(str(wallet_b["available_balance"])) == Decimal("50.00"), "冻结退回可用"
    assert Decimal(str(wallet_b["total_withdrawn"])) == Decimal("0.00")

    refund_txs = await _ledger_of(client, user_b, "WITHDRAWAL_REFUND")
    assert len(refund_txs) == 1
    assert Decimal(str(refund_txs[0]["amount"])) == Decimal("50.00")
    assert refund_txs[0]["withdrawal_id"] == b["id"]

    # 失败通知落库
    notifications = (
        (
            await db_session.execute(
                select(Notification).where(Notification.user_id == user_b["user"]["id"])
            )
        )
        .scalars()
        .all()
    )
    assert len(notifications) == 1, "失败应给申请人发一条通知"
    assert "批量打款失败" in notifications[0].content
    assert str(b["id"]) in notifications[0].content
    assert notifications[0].ref_id == b["id"]

    # ---- 批次关账快照：取实际处理行，忽略回执自报汇总 ----
    detail = await _batch_detail(client, admin_user, batch_id)
    assert detail["status"] == "CLOSED"
    assert detail["imported_by"] == admin_id
    assert detail["imported_at"] is not None
    assert detail["reference_no"] == order_no
    assert detail["success_count"] == 1
    assert Decimal(str(detail["success_amount"])) == Decimal("30.00")
    assert detail["fail_count"] == 1
    assert Decimal(str(detail["fail_amount"])) == Decimal("50.00")
    assert detail["exportable_count"] == 0, "两笔都已终态"
    items = {item["id"]: item for item in detail["items"]}
    assert items[a["id"]]["payout_result"] == "PAID"
    assert items[b["id"]]["payout_result"] == "REJECTED"


# =============================================================================
# 15. 幂等：同一份回执重复导入
# =============================================================================


async def test_import_receipt_is_idempotent(
    client: AsyncClient, admin_user: dict, make_captcha
):
    """重复导入同一份回执：全部安全跳过，资金一动不动。"""
    admin_id = admin_user["user"]["id"]
    user_a, a = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="idem_a@example.com",
        username="IdemA",
        amount=40,
        account_name="王五",
        account_no="idem_a@alipay.test",
    )
    user_b, b = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="idem_b@example.com",
        username="IdemB",
        amount=60,
        account_name="赵六",
        account_no="idem_b@alipay.test",
    )
    resp = await _create_batch(client, admin_user, ids=[a["id"], b["id"]])
    assert resp.status_code == 201, resp.text
    batch_id = resp.json()["id"]

    rows = [
        _receipt_row(
            account_no="idem_a@alipay.test",
            account_name="王五",
            amount=40,
            status="成功",
            order_no="ORD-IDEM-A",
            serial_no="SER-IDEM-A",
            remark=f"GW提现单W{a['id']}",
        ),
        _receipt_row(
            account_no="idem_b@alipay.test",
            account_name="赵六",
            amount=60,
            status="失败",
            order_no="ORD-IDEM-B",
            remark=f"GW提现单W{b['id']}",
            fail_reason="账号户名不符",
        ),
    ]
    content = build_receipt_xls(rows, "20260101990002")

    resp = await _import_receipt(
        client, admin_user, batch_id, filename="receipt.xls", content=content
    )
    assert resp.status_code == 200, resp.text
    first = resp.json()
    assert (first["success"], first["failed"], first["skipped"], first["unmatched"]) == (
        1,
        1,
        0,
        0,
    ), first

    # 第一次导入后的资金快照
    wallet_a = await _wallet_of(client, user_a)
    wallet_b = await _wallet_of(client, user_b)
    paid_txs = await _ledger_of(client, user_a, "WITHDRAWAL_PAID")
    refund_txs = await _ledger_of(client, user_b, "WITHDRAWAL_REFUND")
    assert len(paid_txs) == 1
    assert len(refund_txs) == 1
    a_row = await _admin_withdrawal(client, admin_user, a["id"])
    b_row = await _admin_withdrawal(client, admin_user, b["id"])

    # 原封不动再导入一次
    resp = await _import_receipt(
        client, admin_user, batch_id, filename="receipt.xls", content=content
    )
    assert resp.status_code == 200, resp.text
    second = resp.json()
    assert second["success"] == 0, second
    assert second["failed"] == 0, second
    assert second["unmatched"] == 0, second
    assert second["skipped"] == 2, second
    assert len(second["items"]) == 2
    for item in second["items"]:
        assert item["result"] == "SKIPPED", item
        assert "重复导入" in (item["reason"] or ""), item

    # 资金完全没动
    again_a = await _wallet_of(client, user_a)
    again_b = await _wallet_of(client, user_b)
    assert again_a == wallet_a, "重复导入后 A 的钱包必须一模一样"
    assert again_b == wallet_b, "重复导入后 B 的钱包必须一模一样"
    assert len(await _ledger_of(client, user_a, "WITHDRAWAL_PAID")) == 1
    assert len(await _ledger_of(client, user_b, "WITHDRAWAL_REFUND")) == 1
    assert await _admin_withdrawal(client, admin_user, a["id"]) == a_row
    assert await _admin_withdrawal(client, admin_user, b["id"]) == b_row

    # 批次仍是 CLOSED，重复导入覆盖快照为 0/0
    detail = await _batch_detail(client, admin_user, batch_id)
    assert detail["status"] == "CLOSED"
    assert detail["imported_by"] == admin_id
    assert detail["reference_no"] == "20260101990002"


# =============================================================================
# 16. 匹配规则
# =============================================================================


async def test_receipt_matches_by_account_when_remark_missing(
    client: AsyncClient, admin_user: dict, make_captcha
):
    """16a：没有备注 + 序号错乱时，按 账号+姓名+金额 唯一匹配。"""
    _, w = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="match_a@example.com",
        username="MatchA",
        amount=80,
        account_name="孙七",
        account_no="match_a@alipay.test",
    )
    resp = await _create_batch(client, admin_user, ids=[w["id"]])
    assert resp.status_code == 201, resp.text
    batch_id = resp.json()["id"]

    rows = [
        _receipt_row(
            account_no="match_a@alipay.test",
            account_name="孙七",
            amount=80,
            status="成功",
            seq="序号乱码",
            order_no="ORD-MATCH-A",
            serial_no="SER-MATCH-A",
        )
    ]
    resp = await _import_receipt(
        client,
        admin_user,
        batch_id,
        filename="receipt.xls",
        content=build_receipt_xls(rows, "20260101990003"),
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["success"] == 1, result
    assert result["unmatched"] == 0, result
    assert result["items"][0]["withdrawal_id"] == w["id"]

    row = await _admin_withdrawal(client, admin_user, w["id"])
    assert row["status"] == "PAID"
    assert row["payment_reference"] == "SER-MATCH-A"


async def test_payment_reference_falls_back_to_order_no(
    client: AsyncClient, admin_user: dict, make_captcha
):
    """回执没给流水号时，支付凭证退化为订单号。"""
    _, w = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="fallback_a@example.com",
        username="FallbackA",
        amount=75,
        account_name="冯十二",
        account_no="fallback_a@alipay.test",
    )
    resp = await _create_batch(client, admin_user, ids=[w["id"]])
    assert resp.status_code == 201, resp.text
    batch_id = resp.json()["id"]

    rows = [
        _receipt_row(
            account_no="fallback_a@alipay.test",
            account_name="冯十二",
            amount=75,
            status="成功",
            order_no="ORD-FALLBACK-A",
            serial_no="",
            remark=f"GW提现单W{w['id']}",
        )
    ]
    resp = await _import_receipt(
        client,
        admin_user,
        batch_id,
        filename="receipt.xls",
        content=build_receipt_xls(rows, "20260101990011"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] == 1, resp.text

    row = await _admin_withdrawal(client, admin_user, w["id"])
    assert row["status"] == "PAID"
    assert row["payment_reference"] == "ORD-FALLBACK-A", "流水号为空时取订单号"


async def test_receipt_duplicate_rows_do_not_double_pay(
    client: AsyncClient, admin_user: dict, make_captcha
):
    """16b：两行完全相同的回执行只可能命中一笔，另一行判为未匹配（绝不二次动钱）。"""
    user, w = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="dup_a@example.com",
        username="DupA",
        amount=90,
        account_name="周八",
        account_no="dup_a@alipay.test",
    )
    resp = await _create_batch(client, admin_user, ids=[w["id"]])
    assert resp.status_code == 201, resp.text
    batch_id = resp.json()["id"]

    rows = [
        _receipt_row(
            account_no="dup_a@alipay.test",
            account_name="周八",
            amount=90,
            status="成功",
            order_no="ORD-DUP-A1",
            serial_no="SER-DUP-A1",
        ),
        _receipt_row(
            account_no="dup_a@alipay.test",
            account_name="周八",
            amount=90,
            status="成功",
            order_no="ORD-DUP-A2",
            serial_no="SER-DUP-A2",
        ),
    ]
    resp = await _import_receipt(
        client,
        admin_user,
        batch_id,
        filename="receipt.xls",
        content=build_receipt_xls(rows, "20260101990004"),
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["success"] == 1, result
    assert result["failed"] == 0, result
    assert result["unmatched"] == 1, result
    assert result["skipped"] == 0, result
    results = [item["result"] for item in result["items"]]
    assert sorted(results) == ["PAID", "UNMATCHED"], results

    # 只动了一笔钱
    row = await _admin_withdrawal(client, admin_user, w["id"])
    assert row["status"] == "PAID"
    assert row["payment_reference"] == "SER-DUP-A1", "第一行先命中"
    wallet = await _wallet_of(client, user)
    assert Decimal(str(wallet["total_withdrawn"])) == Decimal("90.00"), "只累计一次"
    assert Decimal(str(wallet["frozen_balance"])) == Decimal("0.00")
    assert len(await _ledger_of(client, user, "WITHDRAWAL_PAID")) == 1


async def test_unknown_status_row_consumes_nothing(
    client: AsyncClient, admin_user: dict, make_captcha
):
    """16c：状态为「处理中」的行只跳过、不占位，后面的成功行仍能匹配到同一笔。"""
    user, w = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="unknown_a@example.com",
        username="UnknownA",
        amount=100,
        account_name="吴九",
        account_no="unknown_a@alipay.test",
    )
    resp = await _create_batch(client, admin_user, ids=[w["id"]])
    assert resp.status_code == 201, resp.text
    batch_id = resp.json()["id"]

    rows = [
        _receipt_row(
            account_no="unknown_a@alipay.test",
            account_name="吴九",
            amount=100,
            status="处理中",
            seq="坏序号",
            order_no="ORD-UNK-A1",
            serial_no="SER-UNK-A1",
        ),
        _receipt_row(
            account_no="unknown_a@alipay.test",
            account_name="吴九",
            amount=100,
            status="成功",
            order_no="ORD-UNK-A2",
            serial_no="SER-UNK-A2",
        ),
    ]
    resp = await _import_receipt(
        client,
        admin_user,
        batch_id,
        filename="receipt.xls",
        content=build_receipt_xls(rows, "20260101990005"),
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["success"] == 1, result
    assert result["skipped"] == 1, result
    assert result["unmatched"] == 0, result
    results = [item["result"] for item in result["items"]]
    assert results == ["SKIPPED", "PAID"], results

    row = await _admin_withdrawal(client, admin_user, w["id"])
    assert row["status"] == "PAID", "后面的成功行仍然打款成功"
    assert row["payment_reference"] == "SER-UNK-A2"
    wallet = await _wallet_of(client, user)
    assert Decimal(str(wallet["total_withdrawn"])) == Decimal("100.00")
    assert len(await _ledger_of(client, user, "WITHDRAWAL_PAID")) == 1

    # 非终态 → payout_result 记 SKIPPED
    detail = await _batch_detail(client, admin_user, batch_id)
    item = detail["items"][0]
    assert item["payout_result"] == "PAID", item


async def test_receipt_for_terminal_withdrawal_skipped(
    client: AsyncClient, admin_user: dict, make_captcha
):
    """16d：批次里的提现已被打款（手工标记）→ 回执行安全跳过，不重复动钱。"""
    user, w = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="terminal_a@example.com",
        username="TerminalA",
        amount=110,
        account_name="郑十",
        account_no="terminal_a@alipay.test",
    )
    await _approve(client, admin_user, w["id"])
    await _mark_paid(client, admin_user, w["id"], "MANUAL-PAID-2")

    resp = await _create_batch(client, admin_user, ids=[w["id"]])
    assert resp.status_code == 400, "已打款的提现不允许建批次"

    # 换成先建批次、再手工打款的路径（批次内提现随后变成终态）
    user_b, w2 = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="terminal_b@example.com",
        username="TerminalB",
        amount=120,
        account_name="钱十一",
        account_no="terminal_b@alipay.test",
    )
    resp = await _create_batch(
        client, admin_user, ids=[w2["id"]], remark="终态提现批次"
    )
    assert resp.status_code == 201, resp.text
    batch_id = resp.json()["id"]
    await _approve(client, admin_user, w2["id"])
    await _mark_paid(client, admin_user, w2["id"], "MANUAL-PAID-3")
    wallet_before = await _wallet_of(client, user_b)
    ledger_before = await _ledger_of(client, user_b, "WITHDRAWAL_PAID")

    rows = [
        _receipt_row(
            account_no="terminal_b@alipay.test",
            account_name="钱十一",
            amount=120,
            status="成功",
            order_no="ORD-TERM-B",
            serial_no="SER-TERM-B",
        )
    ]
    resp = await _import_receipt(
        client,
        admin_user,
        batch_id,
        filename="receipt.xls",
        content=build_receipt_xls(rows, "20260101990006"),
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["success"] == 0, result
    assert result["failed"] == 0, result
    assert result["skipped"] == 1, result
    assert result["items"][0]["result"] == "SKIPPED", result["items"]

    row = await _admin_withdrawal(client, admin_user, w2["id"])
    assert row["status"] == "PAID"
    assert row["payment_reference"] == "MANUAL-PAID-3", "凭证号不被回执覆盖"
    assert await _wallet_of(client, user_b) == wallet_before, "不能重复扣款"
    assert await _ledger_of(client, user_b, "WITHDRAWAL_PAID") == ledger_before
    assert user["user"]["id"] != user_b["user"]["id"]


# =============================================================================
# 17. CSV 回执（UTF-8 BOM / GBK）
# =============================================================================


async def test_import_receipt_csv_utf8_with_bom(
    client: AsyncClient, admin_user: dict, make_captcha
):
    """CSV 回执（utf-8 + BOM）与 .xls 走同一套结果。"""
    user_a, a = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="csv_a@example.com",
        username="CsvA",
        amount=35,
        account_name="csv张三",
        account_no="csv_a@alipay.test",
    )
    user_b, b = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="csv_b@example.com",
        username="CsvB",
        amount=45,
        account_name="csv李四",
        account_no="csv_b@alipay.test",
    )
    resp = await _create_batch(client, admin_user, ids=[a["id"], b["id"]])
    assert resp.status_code == 201, resp.text
    batch_id = resp.json()["id"]

    rows = [
        _receipt_row(
            account_no="csv_a@alipay.test",
            account_name="csv张三",
            amount=35,
            status="成功",
            order_no="ORD-CSV-A",
            serial_no="SER-CSV-A",
            remark=f"GW提现单W{a['id']}",
        ),
        _receipt_row(
            account_no="csv_b@alipay.test",
            account_name="csv李四",
            amount=45,
            status="失败",
            order_no="ORD-CSV-B",
            remark=f"GW提现单W{b['id']}",
            fail_reason="账户不存在",
        ),
    ]
    resp = await _import_receipt(
        client,
        admin_user,
        batch_id,
        filename="receipt.csv",
        content=build_receipt_csv(rows, "20260101990007"),
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert (result["success"], result["failed"], result["unmatched"], result["skipped"]) == (
        1,
        1,
        0,
        0,
    ), result
    assert result["reference_no"] == "20260101990007"

    a_row = await _admin_withdrawal(client, admin_user, a["id"])
    assert a_row["status"] == "PAID"
    assert a_row["payment_reference"] == "SER-CSV-A"
    b_row = await _admin_withdrawal(client, admin_user, b["id"])
    assert b_row["status"] == "REJECTED"
    assert b_row["reject_reason"].startswith("批量打款失败：")

    wallet_a = await _wallet_of(client, user_a)
    assert Decimal(str(wallet_a["total_withdrawn"])) == Decimal("35.00")
    wallet_b = await _wallet_of(client, user_b)
    assert Decimal(str(wallet_b["available_balance"])) == Decimal("45.00")

    detail = await _batch_detail(client, admin_user, batch_id)
    assert detail["status"] == "CLOSED"
    assert detail["success_count"] == 1
    assert detail["fail_count"] == 1


async def test_import_receipt_csv_gbk(
    client: AsyncClient, admin_user: dict, make_captcha
):
    """GBK 编码的 CSV 回执同样能解析。"""
    user, w = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="gbk_a@example.com",
        username="GbkA",
        amount=55,
        account_name="gbk王五",
        account_no="gbk_a@alipay.test",
    )
    resp = await _create_batch(client, admin_user, ids=[w["id"]])
    assert resp.status_code == 201, resp.text
    batch_id = resp.json()["id"]

    rows = [
        _receipt_row(
            account_no="gbk_a@alipay.test",
            account_name="gbk王五",
            amount=55,
            status="成功",
            order_no="ORD-GBK-A",
            serial_no="SER-GBK-A",
        )
    ]
    resp = await _import_receipt(
        client,
        admin_user,
        batch_id,
        filename="receipt.csv",
        content=build_receipt_csv(rows, "20260101990008", encoding="gbk"),
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["success"] == 1, result
    assert result["reference_no"] == "20260101990008"

    row = await _admin_withdrawal(client, admin_user, w["id"])
    assert row["status"] == "PAID"
    assert row["payment_reference"] == "SER-GBK-A"
    wallet = await _wallet_of(client, user)
    assert Decimal(str(wallet["total_withdrawn"])) == Decimal("55.00")


# =============================================================================
# 18. 导入护栏
# =============================================================================


async def test_import_receipt_guard_rails(
    client: AsyncClient, admin_user: dict, registered_user: dict, make_captcha
):
    """文件类型、大小、权限三道护栏。"""
    _, w = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="guard_a@example.com",
        username="GuardA",
        amount=30,
    )
    resp = await _create_batch(client, admin_user, ids=[w["id"]])
    assert resp.status_code == 201, resp.text
    batch_id = resp.json()["id"]

    # 只支持 .xls / .csv
    for filename in ("receipt.txt", "receipt.xlsx"):
        resp = await _import_receipt(
            client,
            admin_user,
            batch_id,
            filename=filename,
            content=b"whatever",
        )
        assert resp.status_code == 400, f"{filename} 应被拒：{resp.text}"
        assert "仅支持 .xls 或 .csv" in resp.json()["detail"], resp.text

    # 超过 5MB
    resp = await _import_receipt(
        client,
        admin_user,
        batch_id,
        filename="receipt.xls",
        content=b"x" * (5 * 1024 * 1024 + 16),
    )
    assert resp.status_code == 400, resp.text
    assert "5MB" in resp.json()["detail"], resp.text

    # 非管理员
    resp = await _import_receipt(
        client,
        registered_user,
        batch_id,
        filename="receipt.xls",
        content=build_receipt_xls(
            [
                _receipt_row(
                    account_no=w["account_no"],
                    account_name=w["account_name"],
                    amount=30,
                    status="成功",
                )
            ],
            "20260101990009",
        ),
    )
    assert resp.status_code == 403, resp.text

    # 批次不存在 → 404（先于文件类型校验）
    resp = await _import_receipt(
        client, admin_user, 999999, filename="receipt.xls", content=b"whatever"
    )
    assert resp.status_code == 404, resp.text

    # 列表 / 详情同样要管理员
    for url in (
        "/admin/withdrawals/payout-batches",
        f"/admin/withdrawals/payout-batches/{batch_id}",
    ):
        resp = await client.get(url, headers=auth_header(registered_user))
        assert resp.status_code == 403, f"{url} 非管理员应 403"

    # 前面的失败导入没有造成任何变动
    row = await _admin_withdrawal(client, admin_user, w["id"])
    assert row["status"] == "PENDING"


# =============================================================================
# 19. payout_result 字段
# =============================================================================


async def test_payout_result_field_after_import(
    client: AsyncClient, admin_user: dict, make_captcha
):
    """OPEN 批次 payout_result 为 null；导入后 PAID / REJECTED / SKIPPED 各就各位。"""
    _, a = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="result_a@example.com",
        username="ResultA",
        amount=30,
        account_name="结果甲",
        account_no="result_a@alipay.test",
    )
    _, b = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="result_b@example.com",
        username="ResultB",
        amount=40,
        account_name="结果乙",
        account_no="result_b@alipay.test",
    )
    _, c = await _make_withdrawal(
        client,
        admin_user,
        make_captcha,
        email="result_c@example.com",
        username="ResultC",
        amount=50,
        account_name="结果丙",
        account_no="result_c@alipay.test",
    )
    resp = await _create_batch(client, admin_user, ids=[a["id"], b["id"], c["id"]])
    assert resp.status_code == 201, resp.text
    batch_id = resp.json()["id"]

    # OPEN：还没有回执，判不出结果
    detail = await _batch_detail(client, admin_user, batch_id)
    assert all(item["payout_result"] is None for item in detail["items"]), detail["items"]
    assert detail["exportable_count"] == 3

    rows = [
        _receipt_row(
            account_no="result_a@alipay.test",
            account_name="结果甲",
            amount=30,
            status="成功",
            order_no="ORD-RES-A",
            serial_no="SER-RES-A",
        ),
        _receipt_row(
            account_no="result_b@alipay.test",
            account_name="结果乙",
            amount=40,
            status="失败",
            order_no="ORD-RES-B",
            fail_reason="账户不存在",
        ),
        _receipt_row(
            account_no="result_c@alipay.test",
            account_name="结果丙",
            amount=50,
            status="处理中",
            order_no="ORD-RES-C",
        ),
    ]
    resp = await _import_receipt(
        client,
        admin_user,
        batch_id,
        filename="receipt.xls",
        content=build_receipt_xls(rows, "20260101990010"),
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert (result["success"], result["failed"], result["skipped"]) == (1, 1, 1), result

    detail = await _batch_detail(client, admin_user, batch_id)
    results = {item["id"]: item["payout_result"] for item in detail["items"]}
    assert results[a["id"]] == "PAID", results
    assert results[b["id"]] == "REJECTED", results
    assert results[c["id"]] == "SKIPPED", "状态异常、没动钱的那笔记 SKIPPED"
    statuses = {item["id"]: item["status"] for item in detail["items"]}
    assert statuses[c["id"]] == "PENDING", "SKIPPED 的提现保持原状态"
    assert detail["exportable_count"] == 1
    assert detail["success_count"] == 1
    assert detail["fail_count"] == 1


# =============================================================================
# 20. 批次列表 / 详情分页
# =============================================================================


async def test_payout_batch_list_and_detail_pagination(
    client: AsyncClient, admin_user: dict, make_captcha
):
    """批次列表分页 + 详情的可导出笔数。"""
    created_ids: list[int] = []
    for index in range(3):
        _, withdrawal = await _make_withdrawal(
            client,
            admin_user,
            make_captcha,
            email=f"page_{index}@example.com",
            username=f"Page{index}",
            amount=10 + index,
        )
        resp = await _create_batch(
            client,
            admin_user,
            ids=[withdrawal["id"]],
            remark=f"第{index + 1}批",
        )
        assert resp.status_code == 201, resp.text
        created_ids.append(resp.json()["id"])

    resp = await client.get(
        "/admin/withdrawals/payout-batches?page=1&page_size=2",
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 3
    assert data["pages"] == 2
    assert data["page"] == 1
    assert data["page_size"] == 2
    assert len(data["items"]) == 2
    assert [item["id"] for item in data["items"]] == created_ids[::-1][:2], "时间倒序"
    assert all(item["status"] == "OPEN" for item in data["items"])
    assert all(Decimal(str(item["total_amount"])) >= Decimal("10.00") for item in data["items"])

    resp = await client.get(
        "/admin/withdrawals/payout-batches?page=2&page_size=2",
        headers=auth_header(admin_user),
    )
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == created_ids[0]

    # 越界页码返回空列表
    resp = await client.get(
        "/admin/withdrawals/payout-batches?page=5&page_size=2",
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == []

    # 详情：汇总 + 可导出笔数 + 明细
    detail = await _batch_detail(client, admin_user, created_ids[0])
    assert detail["id"] == created_ids[0]
    assert detail["item_count"] == 1
    assert Decimal(str(detail["total_amount"])) == Decimal("10.00")
    assert detail["exportable_count"] == 1
    assert len(detail["items"]) == 1
    assert detail["items"][0]["amount"] == "10.00"
    assert detail["items"][0]["payout_result"] is None
    assert detail["items"][0]["status"] == "PENDING"
    assert detail["remark"] == "第1批"
