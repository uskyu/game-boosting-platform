"""提现打款批次（支付宝/微信批量转账）与回执导入相关 schemas。"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.models.withdrawal_payout_batch import (
    WithdrawalPayoutBatchStatus,
    WithdrawalPayoutCategory,
)
from app.schemas.serializers import serialize_datetime_utc

# =============================================================================
# 渠道选项（前端提现表单用）
# =============================================================================


class WithdrawalChannelOptionResponse(BaseModel):
    """单个提现渠道选项。"""

    channel: str = Field(description="渠道标识：ALIPAY / WECHAT / BANK")
    label: str = Field(description="渠道中文名")
    enabled: bool = Field(description="当前是否开放（BANK 始终为 True）")


class WithdrawalChannelOptionsResponse(BaseModel):
    """提现渠道选项列表。"""

    items: list[WithdrawalChannelOptionResponse] = Field(description="渠道选项列表")


# =============================================================================
# 打款批次
# =============================================================================


class PayoutBatchCreateRequest(BaseModel):
    """创建打款批次。"""

    ids: list[int] = Field(
        min_length=1,
        max_length=3000,
        description="要加入批次的提现ID列表（去重后至少 1 笔、最多 3000 笔）",
    )
    category: WithdrawalPayoutCategory = Field(
        description="打款渠道分类：ALIPAY / WECHAT",
    )
    remark: str | None = Field(
        default=None,
        max_length=120,
        description="批次备注；留空则默认「M月D号提现打款」",
    )

    model_config = ConfigDict(extra="forbid")


class PayoutBatchItemResponse(BaseModel):
    """批次内的单条提现明细。"""

    id: int = Field(description="提现ID")
    user_id: int = Field(description="申请用户ID")
    username: str | None = Field(default=None, description="申请人用户名")
    user_email: str | None = Field(default=None, description="申请人邮箱")
    amount: Decimal = Field(description="提现金额")
    channel: str = Field(description="收款渠道")
    account_name: str = Field(description="收款人姓名")
    account_no: str = Field(description="收款账号")
    status: str = Field(description="提现状态")
    created_at: datetime = Field(description="申请时间")
    payout_result: str | None = Field(default=None, description="回执导入后的打款结果：PAID / REJECTED / SKIPPED；批次未导回执时为 null")

    @field_serializer("created_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)

    model_config = ConfigDict(from_attributes=True)


class PayoutBatchSummaryResponse(BaseModel):
    """打款批次汇总。"""

    id: int = Field(description="批次ID")
    category: WithdrawalPayoutCategory = Field(description="打款渠道分类")
    remark: str = Field(description="批次备注")
    item_count: int = Field(description="批次笔数")
    total_amount: Decimal = Field(description="批次总金额")
    status: WithdrawalPayoutBatchStatus = Field(description="批次状态：OPEN / CLOSED")
    created_by: int | None = Field(default=None, description="创建管理员ID")
    created_at: datetime = Field(description="创建时间")
    imported_at: datetime | None = Field(default=None, description="导入回执时间")
    imported_by: int | None = Field(default=None, description="导入回执管理员ID")
    reference_no: str | None = Field(default=None, description="渠道批次号")
    success_count: int | None = Field(default=None, description="导入回执时实际成功笔数")
    success_amount: Decimal | None = Field(default=None, description="导入回执时实际成功金额")
    fail_count: int | None = Field(default=None, description="导入回执时实际失败笔数")
    fail_amount: Decimal | None = Field(default=None, description="导入回执时实际失败金额")

    @field_serializer("created_at", "imported_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)

    model_config = ConfigDict(from_attributes=True)


class PayoutBatchDetailResponse(PayoutBatchSummaryResponse):
    """打款批次详情 = 汇总 + 可导出笔数 + 明细列表。"""

    exportable_count: int = Field(description="仍可导出的提现笔数（待审核/待打款）")
    items: list[PayoutBatchItemResponse] = Field(description="批次明细（按提现ID升序）")


class PayoutBatchListResponse(BaseModel):
    """分页的打款批次汇总。"""

    items: list[PayoutBatchSummaryResponse] = Field(description="批次汇总列表")
    total: int = Field(description="总数量")
    page: int = Field(description="当前页码")
    page_size: int = Field(description="每页数量")
    pages: int = Field(description="总页数")


# =============================================================================
# 可全选的提现（后台「全选当前筛选」）
# =============================================================================


class SelectableWithdrawalItem(BaseModel):
    """可加入批次的单条提现（只要 id + 金额，够前端全选用）。"""

    id: int = Field(description="提现ID")
    amount: Decimal = Field(description="提现金额")


class SelectableWithdrawalsResponse(BaseModel):
    """当前筛选（状态 / 渠道）下可加入打款批次的提现汇总。

    命中笔数超过单批上限时只回汇总（items 为空、exceeded=true），
    前端据此禁用「全选当前筛选」并提示先缩小筛选范围分批创建。
    """

    items: list[SelectableWithdrawalItem] = Field(description="可勾选的提现列表（按提现ID升序）；超过单批上限时为空")
    count: int = Field(description="当前筛选下符合条件的提现笔数")
    total_amount: Decimal = Field(description="当前筛选下符合条件的提现金额合计")
    max_items: int = Field(description="单个批次最多允许的笔数")
    exceeded: bool = Field(description="符合条件的笔数是否超过单批上限")


# =============================================================================
# 回执导入结果
# =============================================================================


class ImportReceiptItemResponse(BaseModel):
    """回执中单条明细的处理结果。"""

    withdrawal_id: int | None = Field(default=None, description="匹配到的提现ID（未匹配为 null）")
    seq: int | None = Field(default=None, description="回执里的序号")
    account_no: str = Field(description="回执里的收款账号")
    account_name: str = Field(description="回执里的姓名")
    amount: Decimal | None = Field(default=None, description="回执里的金额")
    result: str = Field(
        description="处理结果：PAID / REJECTED / SKIPPED / UNMATCHED"
    )
    status: str = Field(description="回执侧状态：成功 / 失败 / 其他")
    reason: str | None = Field(default=None, description="跳过/未匹配/驳回原因")


class ImportReceiptResultResponse(BaseModel):
    """导入回执的汇总结果。"""

    batch_id: int = Field(description="批次ID")
    batch_status: WithdrawalPayoutBatchStatus = Field(description="导入后的批次状态")
    reference_no: str | None = Field(default=None, description="渠道批次号")
    total_rows: int = Field(description="回执明细总行数")
    success: int = Field(description="实际处理为已打款的笔数")
    failed: int = Field(description="实际处理为驳回的笔数")
    skipped: int = Field(description="安全跳过的笔数（重复导入/非成功失败）")
    unmatched: int = Field(description="未能匹配的笔数")
    items: list[ImportReceiptItemResponse] = Field(description="逐条处理结果")
    receipt_header: dict | None = Field(default=None, description="回执抬头信息（如有）")
