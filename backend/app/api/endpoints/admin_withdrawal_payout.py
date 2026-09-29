"""后台「提现打款批次」：批量转账文件的创建/导出与回执导入。

流程：管理员选一批审核通过的提现建批次 → 导出 .xls 上传给支付宝/微信 →
渠道返回结果文件 → 导入回执（成功标记已打款、失败驳回退回）。

所有资金动作都在 service 内经 WalletService 原语完成，本层只编排、不直接
改余额；提交由请求的数据库会话依赖在请求结束时统一处理（endpoint 不显式 commit）。
"""

import re
from typing import Annotated
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)

from app.api.deps import DatabaseSession, get_current_admin
from app.models.user import User
from app.models.withdrawal import WithdrawalChannel, WithdrawalStatus
from app.schemas.withdrawal_payout import (
    ImportReceiptResultResponse,
    PayoutBatchCreateRequest,
    PayoutBatchDetailResponse,
    PayoutBatchListResponse,
    PayoutBatchSummaryResponse,
    SelectableWithdrawalsResponse,
)
from app.services.withdrawal_payout_service import WithdrawalPayoutService

router = APIRouter(prefix="/admin", tags=["admin-withdrawal-payout"])

# 回执文件大小上限：5MB。
_MAX_RECEIPT_BYTES = 5 * 1024 * 1024


def _pages(total: int, page_size: int) -> int:
    return (total + page_size - 1) // page_size if total > 0 else 0


def _content_disposition(filename: str) -> str:
    """构造安全的 Content-Disposition。

    批次备注默认是中文（「M月D号提现打款」），而 Starlette 用 latin-1 编码
    响应头，原样把中文拼进 ``filename`` 会让整个导出响应编码失败（500）。
    这里给出 ASCII 兜底名 + RFC 5987 的 UTF-8 百分号编码名，浏览器优先使用
    后者，既不丢中文文件名也不炸响应头。
    """
    # 引号/反斜杠会破坏带引号的 filename 参数
    clean = filename.replace('"', "'").replace("\\", "_")
    ascii_fallback = re.sub(r"[^ -~]+", "_", clean).strip("_")
    if not re.search(r"[A-Za-z0-9]", ascii_fallback):
        ascii_fallback = "payout.xls"
    if ascii_fallback == clean:
        return f'attachment; filename="{clean}"'
    return (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quote(clean)}"
    )


@router.get(
    "/withdrawals/payout-batches",
    response_model=PayoutBatchListResponse,
    summary="打款批次列表",
    description="分页获取提现打款批次汇总（时间倒序）。",
)
async def list_payout_batches(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PayoutBatchListResponse:
    svc = WithdrawalPayoutService(db)
    summaries, total = await svc.list_batch_summaries(page=page, page_size=page_size)
    return PayoutBatchListResponse(
        items=[PayoutBatchSummaryResponse.model_validate(item) for item in summaries],
        total=total,
        page=page,
        page_size=page_size,
        pages=_pages(total, page_size),
    )


@router.post(
    "/withdrawals/payout-batches",
    response_model=PayoutBatchDetailResponse,
    status_code=201,
    summary="创建打款批次",
    description="把一批同渠道、审核通过的提现归入一个打款批次（不动钱，只关联）。",
)
async def create_payout_batch(
    payload: PayoutBatchCreateRequest,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> PayoutBatchDetailResponse:
    svc = WithdrawalPayoutService(db)
    batch = await svc.create_batch(
        current_admin,
        ids=payload.ids,
        category=payload.category,
        remark=payload.remark,
    )
    detail = await svc.get_batch_detail(batch.id)
    return PayoutBatchDetailResponse.model_validate(detail)


@router.get(
    "/withdrawal-payout/selectable-withdrawals",
    response_model=SelectableWithdrawalsResponse,
    summary="可全选的提现汇总",
    description=(
        "按状态/渠道筛选，返回当前可加入打款批次的提现（待审核/待打款，id 升序）"
        "与笔数、合计金额；命中数超过单批上限时只返回汇总，供后台「全选当前筛选」使用。"
    ),
)
async def list_selectable_withdrawals(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    status_filter: WithdrawalStatus | None = Query(default=None, alias="status"),
    channel_filter: WithdrawalChannel | None = Query(default=None, alias="channel"),
) -> SelectableWithdrawalsResponse:
    svc = WithdrawalPayoutService(db)
    return SelectableWithdrawalsResponse.model_validate(
        await svc.list_selectable(
            status_filter=status_filter,
            channel_filter=channel_filter,
        )
    )


@router.get(
    "/withdrawals/payout-batches/{batch_id}",
    response_model=PayoutBatchDetailResponse,
    summary="打款批次详情",
    description="获取单个打款批次的汇总、可导出笔数与明细列表。",
)
async def get_payout_batch(
    batch_id: int,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> PayoutBatchDetailResponse:
    svc = WithdrawalPayoutService(db)
    detail = await svc.get_batch_detail(batch_id)
    return PayoutBatchDetailResponse.model_validate(detail)


@router.get(
    "/withdrawals/payout-batches/{batch_id}/export.xls",
    summary="导出批量转账文件",
    description="导出渠道批量转账上传文件（.xls，含渠道模板表头）。",
)
async def export_payout_batch(
    batch_id: int,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> Response:
    svc = WithdrawalPayoutService(db)
    content, filename = await svc.export_batch_xls(batch_id)
    return Response(
        content=content,
        media_type="application/vnd.ms-excel",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@router.post(
    "/withdrawals/payout-batches/{batch_id}/import-receipt",
    response_model=ImportReceiptResultResponse,
    summary="导入打款回执",
    description=(
        "导入渠道返回的批量转账回执（.xls / .csv，<=5MB）：成功标记已打款、"
        "失败驳回退回；重复导入时已处理的提现一律安全跳过。"
    ),
)
async def import_payout_receipt(
    batch_id: int,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    file: UploadFile = File(..., description="渠道批量转账回执文件"),
) -> ImportReceiptResultResponse:
    content = await file.read()
    if len(content) > _MAX_RECEIPT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="回执文件不能超过 5MB",
        )
    svc = WithdrawalPayoutService(db)
    result = await svc.import_receipt(
        current_admin,
        batch_id,
        content,
        file.filename or "",
    )
    return ImportReceiptResultResponse.model_validate(result)
