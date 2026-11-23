"""浏览器直传 COS：预签名 + 落地提交。

背景（2026-11-24）：对象存储上线后只有**下载**走了 COS 直连（7 天签名 URL），
**上传**仍是「浏览器 -> 后端 multipart -> Pillow 校验/压缩 -> COS」的双跳。
用户的初衷是让图片上传不经服务器，双跳意味着上传带宽仍压在服务器上。

直传流程（COS 模式）：
1. 前端 ``POST /uploads/presign``：声明 scope/文件名/类型/大小；
   后端校验权限与数量上限，生成随机 key，返回 PUT 预签名 URL（默认 10 分钟）；
2. 浏览器把（已本地压缩过的）图片直接 PUT 到 COS，服务器零带宽；
3. 前端 ``POST /uploads/commit``：后端 HEAD 对象确认「确实传到了」，
   复核大小/类型后写入业务字段（订单附件 / 交付凭证）。

LOCAL 模式或预签名失败时，前端继续走原有的 multipart 端点，行为完全不变。
预签名 PUT 不能限制体积，所以 commit 必须再核对一次大小（10MB 上限）。
"""

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DatabaseSession
from app.api.endpoints.orders import (
    _attachment_items,
    _delivery_attachment_items,
)
from app.models.order import Order
from app.models.user import User, UserRole
from app.schemas.order import OrderAttachment, OrderDeliveryAttachment
from app.services import storage_service
from app.services.order_service import get_order_service

router = APIRouter(prefix="/uploads", tags=["直传"])

_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_ATTACHMENTS = 5
_SUFFIX_BY_TYPE = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
_SCOPE_ORDER_ATTACHMENT = "order_attachment"
_SCOPE_DELIVERY_ATTACHMENT = "delivery_attachment"
_ALLOWED_SCOPES = {_SCOPE_ORDER_ATTACHMENT, _SCOPE_DELIVERY_ATTACHMENT}


class PresignRequest(BaseModel):
    scope: str = Field(description="直传用途：order_attachment / delivery_attachment")
    order_id: int = Field(ge=1, description="订单 ID（两个 scope 都需要）")
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=64)
    size: int = Field(ge=1, le=_MAX_IMAGE_BYTES)


class PresignResponse(BaseModel):
    key: str = Field(description="对象 key（相对 /uploads 的路径）")
    upload_url: str = Field(description="PUT 预签名 URL")
    method: str = Field(default="PUT")
    headers: dict[str, str] = Field(default_factory=dict)
    expires_in: int = Field(description="签名有效期（秒）")


class CommitRequest(BaseModel):
    scope: str
    order_id: int = Field(ge=1)
    key: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=64)


_PRESIGN_EXPIRES_SECONDS = 600

# 直传绕过了 PIL 解码，commit 时用魔数确认「确实是图片」。
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_WEBP_RIFF = b"RIFF"


def _looks_like_image(head: bytes, content_type: str) -> bool:
    if content_type == "image/png":
        return head.startswith(_PNG_MAGIC)
    if content_type == "image/jpeg":
        return head.startswith(_JPEG_MAGIC)
    if content_type == "image/webp":
        return head.startswith(_WEBP_RIFF) and head[8:12] == b"WEBP"
    return False


def _normalize_content_type(raw: str) -> str:
    value = (raw or "").split(";")[0].strip().lower()
    if value not in _SUFFIX_BY_TYPE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅支持 PNG、JPEG、WebP 图片",
        )
    return value


def _scope_prefix(scope: str) -> str:
    if scope == _SCOPE_ORDER_ATTACHMENT:
        return "orders/"
    if scope == _SCOPE_DELIVERY_ATTACHMENT:
        return "deliveries/"
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的直传用途"
    )


def _validate_name(name: str) -> str:
    cleaned = Path(name or "").name
    if not cleaned or len(cleaned) > 255:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="附件文件名无效"
        )
    return cleaned


async def _load_order_for_attachment(
    db, order_service, order_id: int, current_user: User
) -> Order:
    order = await order_service.get_order_by_id(order_id, current_user)
    if current_user.role not in (UserRole.ADMIN, UserRole.USER) or (
        current_user.role != UserRole.ADMIN and order.user_id != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="无权修改此订单附件"
        )
    return order


async def _load_claim_for_delivery(db, order_service, order_id: int, current_user: User):
    if current_user.role == UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="管理员不参与交付附件"
        )
    order, claim = await order_service.get_my_claim_for_delivery(order_id, current_user)
    return order, claim


@router.post(
    "/presign",
    response_model=PresignResponse,
    summary="申请图片直传预签名",
    description=(
        "COS 模式返回 PUT 预签名 URL，浏览器直接把图片传到对象存储；"
        "LOCAL 模式返回 409，前端回退到 multipart 上传端点。"
    ),
)
async def presign_upload(
    payload: PresignRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> PresignResponse:
    if payload.scope not in _ALLOWED_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的直传用途"
        )
    content_type = _normalize_content_type(payload.content_type)
    name = _validate_name(payload.filename)
    order_service = get_order_service(db)

    if payload.scope == _SCOPE_ORDER_ATTACHMENT:
        order = await _load_order_for_attachment(
            db, order_service, payload.order_id, current_user
        )
        if len(_attachment_items(order)) >= _MAX_ATTACHMENTS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="订单最多上传5张图片"
            )
    else:
        _order, claim = await _load_claim_for_delivery(
            db, order_service, payload.order_id, current_user
        )
        if len(_delivery_attachment_items(claim.delivery_attachments)) >= _MAX_ATTACHMENTS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="每单最多上传5张交付附件",
            )

    key = f"{_scope_prefix(payload.scope)}{uuid4().hex}{_SUFFIX_BY_TYPE[content_type]}"
    upload_url = await storage_service.presign_put(
        key, content_type, db, expires=_PRESIGN_EXPIRES_SECONDS
    )
    if not upload_url:
        # 当前是本地存储（或 COS 凭据不完整）：直传不可用，前端回退旧路径。
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前未启用对象存储直传，请使用普通上传",
        )
    return PresignResponse(
        key=key,
        upload_url=upload_url,
        headers={"Content-Type": content_type},
        expires_in=_PRESIGN_EXPIRES_SECONDS,
    )


@router.post(
    "/commit",
    response_model=OrderAttachment | OrderDeliveryAttachment,
    summary="确认直传完成并写入订单",
    description=(
        "浏览器直传成功后调用：后端 HEAD 对象核对真实大小与类型，"
        "再写入订单附件或交付凭证。对象不存在或超限一律 400。"
    ),
)
async def commit_upload(
    payload: CommitRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> OrderAttachment | OrderDeliveryAttachment:
    if payload.scope not in _ALLOWED_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的直传用途"
        )
    content_type = _normalize_content_type(payload.content_type)
    name = _validate_name(payload.name)
    prefix = _scope_prefix(payload.scope)
    key = storage_service.normalize_key(payload.key)
    # 只接受后端刚签发的随机 key 形态：scope 前缀 + uuid + 允许后缀。
    if not key.startswith(prefix) or "/" in key[len(prefix):] or ".." in key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="直传对象 key 无效"
        )
    if not key.endswith(_SUFFIX_BY_TYPE[content_type]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="直传对象类型不匹配"
        )

    info = await storage_service.object_info(key, db)
    if info is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="直传文件未找到，请重新上传",
        )
    size, actual_type = info
    if size <= 0:
        await storage_service.delete(key, db)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="直传文件为空"
        )
    if size > _MAX_IMAGE_BYTES:
        await storage_service.delete(key, db)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="图片大小不能超过10MB",
        )
    actual_type = (actual_type or "").split(";")[0].strip().lower()
    if actual_type and actual_type not in (content_type, "application/octet-stream"):
        await storage_service.delete(key, db)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="直传对象类型不匹配"
        )

    # 内容必须是真正的图片：直传没有经过 PIL，这里按魔数兜底。
    head = await storage_service.read_prefix(key, db, length=32)
    if head is None or not _looks_like_image(head, content_type):
        await storage_service.delete(key, db)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="图片内容无效"
        )

    url = f"/uploads/{key}"
    order_service = get_order_service(db)

    if payload.scope == _SCOPE_ORDER_ATTACHMENT:
        order = await _load_order_for_attachment(
            db, order_service, payload.order_id, current_user
        )
        attachments = _attachment_items(order)
        if len(attachments) >= _MAX_ATTACHMENTS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="订单最多上传5张图片"
            )
        item = OrderAttachment(url=url, name=name, size=size, content_type=content_type)
        attachments.append(item)
        order.attachments = [entry.model_dump() for entry in attachments]
        await db.flush()
        return item

    order, claim = await _load_claim_for_delivery(
        db, order_service, payload.order_id, current_user
    )
    items = _delivery_attachment_items(claim.delivery_attachments)
    if len(items) >= _MAX_ATTACHMENTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="每单最多上传5张交付附件",
        )
    item = OrderDeliveryAttachment(
        url=url, name=name, size=size, content_type=content_type
    )
    items.append(item)
    claim.delivery_attachments = [entry.model_dump() for entry in items]
    await db.flush()
    return item
