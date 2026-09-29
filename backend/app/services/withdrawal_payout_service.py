"""提现打款批次：批量转账文件导出 + 渠道回执导入。

管理员从审核通过的提现里选一批，生成批量转账文件上传给渠道（支付宝/微信），
再把渠道返回的结果文件（回执）导入回来：回单状态为「成功」的标记已打款、
「失败」的驳回并解冻退回，汇总信息快照到 WithdrawalPayoutBatch。

资金安全铁律：
- 本服务的每一笔余额变动都经由 WalletService 的既有原语完成
  （review_withdrawal / mark_withdrawal_paid / unfreeze / get_or_create_wallet），
  绝不手工改写 balance。
- 建批次阶段不动钱（提现申请时资金已冻结），只把 withdrawal_requests
  关联到 batch（payout_batch_id）。
- import_receipt 先完整解析+匹配、再统一动钱：解析失败在任何资金变动前
  抛错回滚，绝不「半应用」。重复导入时已处于终态的提现一律 SKIPPED，
  绝不重复动钱（幂等保证，见下方 NOREPEAT 注释）。
"""

import csv
import io
import logging
import re
from collections.abc import Set as AbstractSet
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

import xlrd
import xlwt
from fastapi import HTTPException, status
from sqlalchemy import and_, func, select

from app.core.product_time import PRODUCT_TIMEZONE, product_local_date
from app.models.notification import NotificationType
from app.models.user import User
from app.models.withdrawal import WithdrawalChannel, WithdrawalRequest, WithdrawalStatus
from app.models.withdrawal_payout_batch import (
    WithdrawalPayoutBatch,
    WithdrawalPayoutBatchStatus,
    WithdrawalPayoutCategory,
)
from app.services.withdrawal_channel_service import (
    CHANNEL_LABELS,
    get_or_create_withdrawal_channel_settings,
)
from app.services.wallet_service import WalletService, _to_decimal

logger = logging.getLogger(__name__)

__all__ = ["MAX_BATCH_ITEMS", "WithdrawalPayoutService"]

# 单个批次最多允许的笔数（与 Excel 单文件可承载明细上限一致）。
MAX_BATCH_ITEMS = 3000

# 批次渠道 → 提现渠道（二者共用 ALIPAY/WECHAT 取值）。
_CATEGORY_CHANNEL = {
    WithdrawalPayoutCategory.ALIPAY: WithdrawalChannel.ALIPAY,
    WithdrawalPayoutCategory.WECHAT: WithdrawalChannel.WECHAT,
}

# 提现状态中文标签（用于错误文案 / 结果）。
_STATUS_LABELS = {
    WithdrawalStatus.PENDING: "待审核",
    WithdrawalStatus.APPROVED: "待打款",
    WithdrawalStatus.PAID: "已打款",
    WithdrawalStatus.REJECTED: "已驳回",
}

# 可加入批次 / 可导出 / 可导入处理的「非终态」状态集合。
_OPEN_STATUSES = (WithdrawalStatus.PENDING, WithdrawalStatus.APPROVED)

# 回执结果行「状态」列口径：一律按回执原文语义（成功/失败/其他），
# 本地提现终态由 result 列（打款成功/已驳回/已跳过/未匹配）表达。
_RECEIPT_STATUS_TEXT = {
    "SUCCESS": "成功",
    "FAIL": "失败",
    "UNKNOWN": "其他",
}

_CENT = Decimal("0.01")


def _payout_result_of(
    withdrawal: WithdrawalRequest,
    batch_status: WithdrawalPayoutBatchStatus | None,
) -> str | None:
    """回执导入后，按提现终态反推它在批次里的打款结果。

    批次还没导回执（OPEN）时无从判定，返回 None；导入后：
    已打款=PAID，已驳回=REJECTED，仍停留在非终态=SKIPPED（回执缺行/状态异常）。
    """
    if batch_status is None or batch_status == WithdrawalPayoutBatchStatus.OPEN:
        return None
    if withdrawal.status == WithdrawalStatus.PAID:
        return "PAID"
    if withdrawal.status == WithdrawalStatus.REJECTED:
        return "REJECTED"
    return "SKIPPED"


class WithdrawalPayoutService:
    """打款批次业务逻辑（资金动作全部委托 WalletService 完成）。"""

    def __init__(self, db) -> None:
        self._db = db
        self._wallet = WalletService(db)

    # ------------------------------------------------------------------
    # 建批次 / 查询
    # ------------------------------------------------------------------

    async def create_batch(
        self,
        admin: User,
        *,
        ids: list[int],
        category: WithdrawalPayoutCategory,
        remark: str | None = None,
    ) -> WithdrawalPayoutBatch:
        """把一批审核通过的提现归入一个打款批次（不动钱，只关联）。"""
        # a. 去重并保持顺序
        seen: set[int] = set()
        ordered: list[int] = []
        for value in ids:
            if value not in seen:
                seen.add(value)
                ordered.append(value)
        if not ordered:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="请选择要打款的提现",
            )

        # b. 单批上限
        if len(ordered) > MAX_BATCH_ITEMS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"单次最多 {MAX_BATCH_ITEMS} 笔",
            )

        # c. 渠道分类开关：被后台关闭的分类不能建批次
        label = CHANNEL_LABELS[category.value]
        setting = await get_or_create_withdrawal_channel_settings(self._db)
        enabled = (
            setting.alipay_enabled
            if category == WithdrawalPayoutCategory.ALIPAY
            else setting.wechat_enabled
        )
        if not enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{label}分类已关闭，暂不能创建打款批次",
            )

        # d. 行锁锁住选中的提现（同一事务，行锁到提交）
        result = await self._db.execute(
            select(WithdrawalRequest)
            .where(WithdrawalRequest.id.in_(ordered))
            .with_for_update()
        )
        rows_by_id = {row.id: row for row in result.scalars().all()}

        # e. 缺失的 id（至多点名 5 个，其余记「等 N 笔」）
        missing = [value for value in ordered if value not in rows_by_id]
        if missing:
            names = "、".join(str(value) for value in missing[:5])
            if len(missing) > 5:
                names += f" 等 {len(missing)} 笔"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"以下提现不存在，无法加入打款批次：{names}",
            )

        # f~h. 按传入顺序逐条校验
        required_channel = _CATEGORY_CHANNEL[category]
        selected: list[WithdrawalRequest] = []
        for value in ordered:
            row = rows_by_id[value]
            if row.status not in _OPEN_STATUSES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"提现 #{row.id}（{_STATUS_LABELS[row.status]}）不能加入打款批次",
                )
            if row.channel != required_channel:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"提现 #{row.id} 不是{label}渠道的提现",
                )
            if row.payout_batch_id is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"提现 #{row.id} 已属于批次 #{row.payout_batch_id}",
                )
            selected.append(row)

        # i. 备注：strip，空则用产品时区今天的「M月D号提现打款」，最长 120
        remark_final = (remark or "").strip()
        if not remark_final:
            today = product_local_date(datetime.now(timezone.utc))
            remark_final = f"{today.month}月{today.day}号提现打款"
        remark_final = remark_final[:120]

        total_amount = sum(
            (_to_decimal(row.amount) for row in selected), Decimal("0.00")
        ).quantize(_CENT, rounding=ROUND_HALF_UP)

        # j. 建批次 + 关联提现
        batch = WithdrawalPayoutBatch(
            category=category,
            remark=remark_final,
            created_by=admin.id,
            item_count=len(selected),
            total_amount=total_amount,
        )
        self._db.add(batch)
        await self._db.flush()

        # 此处不动任何资金：提现申请时金额已冻结，建批次只是把提现归集到
        # batch，资金的实际扣减/退回发生在导入回执阶段（经由 WalletService）。
        for row in selected:
            row.payout_batch_id = batch.id
        await self._db.flush()
        await self._db.refresh(batch)

        logger.info(
            "Payout batch %s (%s) created by admin %s with %s items, total=%s",
            batch.id,
            category.value,
            admin.id,
            batch.item_count,
            batch.total_amount,
        )
        return batch

    async def list_batches(
        self, page: int = 1, page_size: int = 20
    ) -> tuple[list[WithdrawalPayoutBatch], int]:
        """分页列出打款批次（时间倒序、id 倒序），与 list_withdrawals 口径一致。"""
        count_result = await self._db.execute(
            select(func.count(WithdrawalPayoutBatch.id))
        )
        total = int(count_result.scalar() or 0)

        result = await self._db.execute(
            select(WithdrawalPayoutBatch)
            .order_by(
                WithdrawalPayoutBatch.created_at.desc(),
                WithdrawalPayoutBatch.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def list_batch_summaries(
        self, page: int = 1, page_size: int = 20
    ) -> tuple[list[dict], int]:
        """分页列出打款批次的汇总 dict（PayoutBatchSummaryResponse 用）。"""
        batches, total = await self.list_batches(page=page, page_size=page_size)
        return [self._batch_summary(batch) for batch in batches], total

    async def get_batch(self, batch_id: int) -> WithdrawalPayoutBatch | None:
        result = await self._db.execute(
            select(WithdrawalPayoutBatch).where(WithdrawalPayoutBatch.id == batch_id)
        )
        return result.scalar_one_or_none()

    async def list_selectable(
        self,
        *,
        status_filter: WithdrawalStatus | None = None,
        channel_filter: WithdrawalChannel | None = None,
    ) -> dict:
        """当前筛选下可加入批次的提现汇总（待审核 / 待打款，按 id 升序）。

        供后台「全选当前筛选」：命中笔数超过单批上限时只回汇总、不回明细，
        前端据此禁用全选按钮并提示先缩小筛选范围分批创建。
        """
        conditions = [WithdrawalRequest.status.in_(_OPEN_STATUSES)]
        if status_filter is not None:
            conditions.append(WithdrawalRequest.status == status_filter)
        if channel_filter is not None:
            conditions.append(WithdrawalRequest.channel == channel_filter)
        criteria = and_(*conditions)

        count_result = await self._db.execute(
            select(func.count(WithdrawalRequest.id)).where(criteria)
        )
        total_count = int(count_result.scalar() or 0)

        # 超上限：明细不回（没有意义，前端也放不下一批），但合计金额仍是一个
        # 便宜的 SQL 聚合，提示文案里要给得出总额
        if total_count > MAX_BATCH_ITEMS:
            sum_result = await self._db.execute(
                select(func.coalesce(func.sum(WithdrawalRequest.amount), 0)).where(
                    criteria
                )
            )
            return {
                "items": [],
                "count": total_count,
                "total_amount": _to_decimal(sum_result.scalar()).quantize(
                    _CENT, rounding=ROUND_HALF_UP
                ),
                "max_items": MAX_BATCH_ITEMS,
                "exceeded": True,
            }

        result = await self._db.execute(
            select(WithdrawalRequest.id, WithdrawalRequest.amount)
            .where(criteria)
            .order_by(WithdrawalRequest.id.asc())
        )
        items = [
            {"id": row_id, "amount": _to_decimal(amount)}
            for row_id, amount in result.all()
        ]
        total_amount = sum(
            (item["amount"] for item in items), Decimal("0.00")
        ).quantize(_CENT, rounding=ROUND_HALF_UP)
        return {
            "items": items,
            "count": len(items),
            "total_amount": total_amount,
            "max_items": MAX_BATCH_ITEMS,
            "exceeded": False,
        }

    async def _load_batch_items(self, batch_id: int) -> list[WithdrawalRequest]:
        """批次内全部提现（按 id 升序）。user 关系是 joined 加载。"""
        result = await self._db.execute(
            select(WithdrawalRequest)
            .where(WithdrawalRequest.payout_batch_id == batch_id)
            .order_by(WithdrawalRequest.id.asc())
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # 序列化助手
    # ------------------------------------------------------------------

    def _batch_summary(self, batch: WithdrawalPayoutBatch) -> dict:
        """批次汇总字段（PayoutBatchSummaryResponse 用）。"""
        return {
            "id": batch.id,
            "category": batch.category.value,
            "remark": batch.remark,
            "item_count": batch.item_count,
            "total_amount": _to_decimal(batch.total_amount),
            "status": batch.status.value,
            "created_by": batch.created_by,
            "created_at": batch.created_at,
            "imported_at": batch.imported_at,
            "imported_by": batch.imported_by,
            "reference_no": batch.reference_no,
            "success_count": batch.success_count,
            "success_amount": batch.success_amount,
            "fail_count": batch.fail_count,
            "fail_amount": batch.fail_amount,
        }

    def _map_batch_item(
        self,
        withdrawal: WithdrawalRequest,
        batch_status: WithdrawalPayoutBatchStatus | None = None,
    ) -> dict:
        """单条提现的明细映射（镜像 admin._map_admin_withdrawal：带 username/email）。"""
        user = withdrawal.user
        return {
            "id": withdrawal.id,
            "user_id": withdrawal.user_id,
            "username": user.username if user is not None else None,
            "user_email": user.email if user is not None else None,
            "amount": _to_decimal(withdrawal.amount),  # 保持 Decimal，交给 schema 序列化
            "channel": withdrawal.channel.value,
            "account_name": withdrawal.account_name,
            "account_no": withdrawal.account_no,
            "status": withdrawal.status.value,
            "created_at": withdrawal.created_at,
            "payout_result": _payout_result_of(withdrawal, batch_status),
        }

    async def get_batch_detail(self, batch_id: int) -> dict:
        """批次详情：汇总 + 可导出笔数 + 明细列表。"""
        batch = await self.get_batch(batch_id)
        if batch is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="打款批次不存在",
            )
        items = await self._load_batch_items(batch_id)
        exportable_count = sum(1 for item in items if item.status in _OPEN_STATUSES)
        detail = self._batch_summary(batch)
        detail["exportable_count"] = exportable_count
        detail["items"] = [
            self._map_batch_item(item, batch.status) for item in items
        ]
        return detail

    # ------------------------------------------------------------------
    # 导出批量转账文件（.xls）
    # ------------------------------------------------------------------

    async def export_batch_xls(self, batch_id: int) -> tuple[bytes, str]:
        """生成渠道批量转账上传文件，返回 (xls_bytes, filename)。"""
        batch = await self.get_batch(batch_id)
        if batch is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="打款批次不存在",
            )
        items = await self._load_batch_items(batch_id)
        exportable = [item for item in items if item.status in _OPEN_STATUSES]
        if not exportable:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该批次暂无可导出的提现",
            )

        is_alipay = batch.category == WithdrawalPayoutCategory.ALIPAY
        workbook = xlwt.Workbook(encoding="utf-8")
        sheet_name = (
            "页一支付宝批量付款文件上传模板"
            if is_alipay
            else "页一微信批量付款文件上传模板"
        )
        sheet = workbook.add_sheet(sheet_name)

        # 第 1 行标题（A1）
        sheet.write(
            0,
            0,
            "支付宝批量付款文件模板（前面两行请勿删除）"
            if is_alipay
            else "微信批量付款文件上传模板（前面两行请勿删除）",
        )

        # 第 H 列（index 7）起的填写说明 / 注意事项：来自客户官方模板，原样复制。
        notes = [
            "填写说明：",
            "注意事项",
            "1.请勿删除或增加列。",
            "2.请勿删除表头，即文件头两行",
            "3.一个文件可以包含3000笔明细，超过3000笔可分多个文件上传；每次可以上传一个文件。",
            "4.上传文件目前支持的Excel版本为1997-2003版本，CSV文件无要求。",
            "5.系统不支持同名文件上传，会提示重复上传，修改文件名后重新上传即可。",
        ]
        for row_index, note in enumerate(notes):
            sheet.write(row_index, 7, note)

        # 第 2 行表头（A-E）。微信无单独模板，沿用支付宝表头文字。
        headers = [
            "序号（必填）",
            "收款方支付宝账号（必填）",
            "收款方姓名（必填）",
            "金额（必填，单位：元）",
            "备注（选填）",
        ]
        for col_index, header in enumerate(headers):
            sheet.write(1, col_index, header)

        # 明细行（第 3 行起，1-based）。金额写成两位小数字符串，避免 Excel 浮点噪声。
        for offset, item in enumerate(exportable):
            row_index = 2 + offset
            sheet.write(row_index, 0, item.id)  # 序号 = 提现 id（整数）
            sheet.write(row_index, 1, item.account_no)  # 收款方账号
            sheet.write(row_index, 2, item.account_name)  # 姓名
            sheet.write(row_index, 3, f"{_to_decimal(item.amount):.2f}")  # 金额（字符串）
            sheet.write(row_index, 4, f"GW提现单W{item.id}")  # 备注（回执据此回填提现 id）

        stream = io.BytesIO()
        workbook.save(stream)
        payload = stream.getvalue()

        date_text = datetime.now(timezone.utc).astimezone(PRODUCT_TIMEZONE).strftime("%Y%m%d")
        file_prefix = "支付宝批量付款" if is_alipay else "微信批量付款"
        filename = f"{file_prefix}_{batch.remark}_{date_text}.xls"
        return payload, filename

    # ------------------------------------------------------------------
    # 导入渠道回执（.xls / .csv）
    # ------------------------------------------------------------------

    async def import_receipt(
        self,
        admin: User,
        batch_id: int,
        file_bytes: bytes,
        filename: str,
    ) -> dict:
        """导入渠道回执：成功的标记已打款、失败的驳回退回，并关账批次。"""
        batch = await self.get_batch(batch_id)
        if batch is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="打款批次不存在",
            )

        lowered = (filename or "").lower()
        if not (lowered.endswith(".xls") or lowered.endswith(".csv")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="仅支持 .xls 或 .csv 回执文件",
            )

        # 匹配池包含批次内全部提现（含终态），好让重复导入时能命中并安全跳过。
        items = await self._load_batch_items(batch_id)
        item_by_id = {item.id: item for item in items}

        # ---- 阶段一：完整解析 + 匹配（此阶段绝不动钱）----
        # 先解析、匹配全部明细；一旦文件畸形就在这里抛 400 并回滚，
        # 保证任何资金变动之前就失败，绝不「半应用」。顺序是刻意为之。
        try:
            detail_rows, reference_no, receipt_header = self._parse_receipt(
                file_bytes, lowered
            )
            plan: list[tuple[dict, WithdrawalRequest | None]] = []
            # 一行一回执：同一笔提现在一次导入里最多被消费一次。命中过就占位，
            # 后面再出现相同 账号+姓名+金额 的行因候选被排除而判为 UNMATCHED，
            # 绝不静默跳过（静默会掩盖渠道文件的重复行，见 _match_row）。
            # 状态非成功/失败（UNKNOWN）的行不产生任何流转，因此不占位，
            # 后面的成功行仍能匹配到同一笔。
            consumed: set[int] = set()
            for row in detail_rows:
                item = self._match_row(row, items, item_by_id, consumed)
                if item is not None and row["status"] != "UNKNOWN":
                    consumed.add(item.id)
                plan.append((row, item))
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一转成 400，解析失败
            logger.exception("Receipt parse failed for batch %s", batch_id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"回执文件解析失败：{exc}",
            ) from exc

        # ---- 阶段二：按计划动钱（全部经由 WalletService 原语）----
        # 延迟导入，避免 service -> api 的循环依赖（同 payout_scheduler）。
        from app.api.notification_utils import notify_user

        result_items: list[dict] = []
        success_count = 0
        success_amount = Decimal("0.00")
        fail_count = 0
        fail_amount = Decimal("0.00")
        skipped = 0
        unmatched = 0

        for row, item in plan:
            base = {
                "seq": row["seq"],
                "account_no": row["account_no"],
                "account_name": row["account_name"],
                "amount": row["amount"],
            }
            if item is None:
                unmatched += 1
                result_items.append(
                    {
                        **base,
                        "withdrawal_id": None,
                        "result": "UNMATCHED",
                        # 回执原文状态（成功/失败/其他）照实带回，
                        # 管理员要看得出渠道侧到底付没付这笔钱
                        "status": _RECEIPT_STATUS_TEXT.get(row["status"], "其他"),
                        "reason": "未能匹配到批次内的提现",
                    }
                )
                continue

            # NOREPEAT：幂等保证。已处于终态的提现一律跳过，绝不重复动钱。
            # 这笔守卫是「重复导入安全跳过」的核心——即使前端重复上传同一回执，
            # 也不会给同一笔提现二次扣款/二次退款。
            if item.status in (WithdrawalStatus.PAID, WithdrawalStatus.REJECTED):
                skipped += 1
                result_items.append(
                    {
                        **base,
                        "withdrawal_id": item.id,
                        "result": "SKIPPED",
                        "status": _RECEIPT_STATUS_TEXT.get(row["status"], "其他"),
                        "reason": "该提现已处理过（重复导入安全跳过）",
                    }
                )
                continue

            if row["status"] == "SUCCESS":
                # 回执即审批：PENDING 的先审核通过，再标记已打款。
                if item.status == WithdrawalStatus.PENDING:
                    await self._wallet.review_withdrawal(item.id, admin, approve=True)
                reference = (
                    row["serial_no"] or row["order_no"] or f"BATCH-{batch.id}"
                )[:128]
                await self._wallet.mark_withdrawal_paid(
                    item.id, admin, payment_reference=reference
                )
                success_count += 1
                success_amount += _to_decimal(item.amount)
                result_items.append(
                    {
                        **base,
                        "withdrawal_id": item.id,
                        "result": "PAID",
                        "status": _RECEIPT_STATUS_TEXT.get(row["status"], "其他"),
                        "reason": reference,
                    }
                )
            elif row["status"] == "FAIL":
                fail_reason = (row["fail_reason"] or "").strip() or "支付宝未说明原因"
                reject_reason = f"批量打款失败：{fail_reason}"[:255]
                if item.reviewed_by is None:
                    item.reviewed_by = admin.id
                    item.reviewed_at = datetime.now(timezone.utc)
                item.status = WithdrawalStatus.REJECTED
                item.reject_reason = reject_reason
                wallet = await self._wallet.get_or_create_wallet(item.user_id)
                await self._wallet.unfreeze(
                    wallet,
                    amount=_to_decimal(item.amount),
                    withdrawal_id=item.id,
                    remark=f"批量打款驳回 #{item.id}",
                )
                fail_count += 1
                fail_amount += _to_decimal(item.amount)
                await notify_user(
                    self._db,
                    user_id=item.user_id,
                    type=NotificationType.APPLICATION_REJECTED,
                    title="提现申请未通过",
                    content=(
                        f"您的提现申请 #{item.id}（¥{_to_decimal(item.amount):.2f}）"
                        f"因批量打款失败已驳回，金额已退回可用余额。原因：{fail_reason}"
                    ),
                    link="/wallet",
                    ref_id=item.id,
                )
                result_items.append(
                    {
                        **base,
                        "withdrawal_id": item.id,
                        "result": "REJECTED",
                        "status": _RECEIPT_STATUS_TEXT.get(row["status"], "其他"),
                        "reason": reject_reason,
                    }
                )
            else:  # UNKNOWN
                skipped += 1
                result_items.append(
                    {
                        **base,
                        "withdrawal_id": item.id,
                        "result": "SKIPPED",
                        "status": _RECEIPT_STATUS_TEXT.get(row["status"], "其他"),
                        "reason": "回执状态非成功/失败，未处理",
                    }
                )

        # 关账：成功/失败计数是「本批实际处理」的 PAID/REJECTED 笔数，不是回执自报汇总。
        batch.status = WithdrawalPayoutBatchStatus.CLOSED
        batch.imported_by = admin.id
        batch.imported_at = datetime.now(timezone.utc)
        if reference_no:
            batch.reference_no = reference_no[:64]
        batch.success_count = success_count
        batch.success_amount = success_amount.quantize(_CENT)
        batch.fail_count = fail_count
        batch.fail_amount = fail_amount.quantize(_CENT)
        await self._db.flush()

        logger.info(
            "Payout batch %s receipt imported by admin %s: total=%s success=%s "
            "fail=%s skipped=%s unmatched=%s ref=%s",
            batch.id,
            admin.id,
            len(detail_rows),
            success_count,
            fail_count,
            skipped,
            unmatched,
            reference_no,
        )
        return {
            "batch_id": batch.id,
            "batch_status": batch.status.value,
            "reference_no": batch.reference_no,
            "total_rows": len(detail_rows),
            "success": success_count,
            "failed": fail_count,
            "skipped": skipped,
            "unmatched": unmatched,
            "items": result_items,
            "receipt_header": receipt_header,
        }

    # ------------------------------------------------------------------
    # 回执解析
    # ------------------------------------------------------------------

    def _parse_receipt(
        self, file_bytes: bytes, lowered: str
    ) -> tuple[list[dict], str | None, dict | None]:
        """解析回执文件，返回 (明细行列表, 批次号, 回执抬头)。"""
        rows = (
            self._parse_csv(file_bytes)
            if lowered.endswith(".csv")
            else self._parse_xls(file_bytes)
        )
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="回执文件格式不正确：内容为空",
            )

        # 明细表头 = 第一行同时含「序号」和「收款账号」的行。
        # （真实支付宝回执的表头区也含一次「订单号」，用「明细表头之前」规则绕开。）
        detail_header_index: int | None = None
        for index, row in enumerate(rows):
            joined = "".join(str(cell).strip() for cell in row)
            if "序号" in joined and "收款账号" in joined:
                detail_header_index = index
                break
        if detail_header_index is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="回执文件格式不正确：未找到明细表头",
            )

        header_cells = [str(cell).strip() for cell in rows[detail_header_index]]
        col_map = self._map_header_columns(header_cells)

        reference_no, receipt_header = self._parse_batch_header(
            rows[:detail_header_index]
        )

        account_idx = col_map.get("收款账号")
        amount_idx = col_map.get("金额")
        detail_rows: list[dict] = []
        for row in rows[detail_header_index + 1 :]:
            account_val = self._cell(row, account_idx)
            amount_val = self._cell(row, amount_idx)
            if not account_val and not amount_val:
                continue
            detail_rows.append(self._parse_detail_row(row, col_map))

        return detail_rows, reference_no, receipt_header

    def _parse_xls(self, file_bytes: bytes) -> list[list[str]]:
        book = xlrd.open_workbook(file_contents=file_bytes)
        sheet = book.sheet_by_index(0)
        return [
            [self._normalize_xls_cell(sheet, r, c) for c in range(sheet.ncols)]
            for r in range(sheet.nrows)
        ]

    @staticmethod
    def _normalize_xls_cell(sheet, r: int, c: int) -> str:
        """xlrd 单元格统一转 str：数字取整优先，空串兜底。"""
        ctype = sheet.cell_type(r, c)
        value = sheet.cell_value(r, c)
        if ctype == 0 or ctype == 6 or value == "":
            return ""
        if ctype == 2:  # 数字
            number = float(value)
            return str(int(number)) if number == int(number) else str(number)
        if ctype == 4:  # 布尔
            return "TRUE" if value else "FALSE"
        if ctype == 5:  # 错误
            return ""
        if ctype == 3:  # 日期（仅作展示用，不参与匹配）
            return str(value)
        return value  # 文本

    def _parse_csv(self, file_bytes: bytes) -> list[list[str]]:
        text: str | None = None
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = file_bytes.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="回执文件解析失败：无法识别的编码",
            )
        return [[str(cell) for cell in record] for record in csv.reader(io.StringIO(text))]

    @staticmethod
    def _map_header_columns(header_cells: list[str]) -> dict[str, int]:
        """按包含关系把目标列名映射到列下标（容忍「金额（元）」这类变体）。"""
        targets = [
            "序号",
            "创建时间",
            "订单号",
            "流水号",
            "收款账号",
            "姓名",
            "金额",
            "状态",
            "备注",
            "失败原因",
        ]
        col_map: dict[str, int] = {}
        for target in targets:
            for index, cell in enumerate(header_cells):
                if target in cell:
                    col_map[target] = index
                    break
        if "状态" not in col_map:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="回执文件格式不正确：缺少状态列",
            )
        return col_map

    @staticmethod
    def _parse_batch_header(
        header_rows: list[list[str]],
    ) -> tuple[str | None, dict | None]:
        """从明细表头之前的抬头区取批次号与汇总字段（均非必需）。"""

        def find_value(label: str) -> str | None:
            for row in header_rows:
                for index, cell in enumerate(row):
                    text = str(cell).strip()
                    if label in text:
                        nxt = next(
                            (
                                str(row[j]).strip()
                                for j in range(index + 1, len(row))
                                if str(row[j]).strip()
                            ),
                            "",
                        )
                        if nxt:
                            return nxt
                        tail = text.split(label, 1)[-1].lstrip("：: ").strip()
                        if tail:
                            return tail
            return None

        order_no = find_value("订单号")
        receipt_header = {
            "order_no": order_no,
            "pay_status": find_value("付款状态"),
            "total_amount": find_value("总金额"),
            "success_amount": find_value("成功金额"),
            "fail_amount": find_value("失败金额"),
        }
        if all(value is None for value in receipt_header.values()):
            return order_no, None
        return order_no, receipt_header

    @staticmethod
    def _cell(row: list[str], index: int | None) -> str:
        if index is None or index < 0 or index >= len(row):
            return ""
        return str(row[index]).strip()

    @staticmethod
    def _parse_detail_row(row: list[str], col_map: dict[str, int]) -> dict:
        def get(target: str) -> str:
            return WithdrawalPayoutService._cell(row, col_map.get(target))

        seq_raw = get("序号")
        try:
            seq: int | None = int(float(seq_raw))
        except (ValueError, TypeError):
            seq = None

        amount_raw = get("金额")
        amount: Decimal | None = None
        if amount_raw:
            try:
                amount = Decimal(amount_raw)
            except Exception:  # noqa: BLE001 - 金额无法解析时保持 None
                amount = None

        status_raw = get("状态")
        if "成功" in status_raw:
            status = "SUCCESS"
        elif "失败" in status_raw:
            status = "FAIL"
        else:
            status = "UNKNOWN"

        return {
            "seq": seq,
            "order_no": get("订单号"),
            "serial_no": get("流水号"),
            "account_no": get("收款账号"),
            "account_name": get("姓名"),
            "amount": amount,
            "status_raw": status_raw,
            "status": status,
            "remark": get("备注"),
            "fail_reason": get("失败原因"),
        }

    @staticmethod
    def _match_row(
        row: dict,
        items: list[WithdrawalRequest],
        item_by_id: dict[int, WithdrawalRequest],
        consumed: AbstractSet[int] = frozenset(),
    ) -> WithdrawalRequest | None:
        """把回执明细行匹配到批次内的提现。优先级：序号==id > 备注里的 W+编号 > 账号唯一匹配。

        ``consumed`` 是本次导入里已被前面的回执行消费过的提现 id：任何匹配
        路径都跳过它们，保证同一笔提现不会被两行回执同时命中（不会二次动钱）。
        """
        # 1) 序号 == 提现 id
        if (
            row["seq"] is not None
            and row["seq"] in item_by_id
            and row["seq"] not in consumed
        ):
            return item_by_id[row["seq"]]

        # 2) 备注里的 W{id}（导出时写入「GW提现单W{id}」）
        matched = re.search(r"W(\d{1,10})", row["remark"] or "")
        if matched:
            withdrawal_id = int(matched.group(1))
            if withdrawal_id in item_by_id and withdrawal_id not in consumed:
                return item_by_id[withdrawal_id]

        # 3) 账号+姓名+金额（±0.005）唯一匹配；2 个以上候选视为歧义，不匹配。
        if row["amount"] is not None:
            candidates = [
                item
                for item in items
                if item.id not in consumed
                and (item.account_no or "").strip() == row["account_no"]
                and (item.account_name or "").strip() == row["account_name"]
                and abs(_to_decimal(item.amount) - row["amount"]) <= Decimal("0.005")
            ]
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) >= 2:
                return None

        return None
