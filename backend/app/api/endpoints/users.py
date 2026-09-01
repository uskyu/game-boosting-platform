"""User profile and booster-application endpoints."""

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DatabaseSession
from app.core.config import settings
from app.models.booster_service import BoosterService
from app.models.user import BoosterApplicationStatus, User, UserRole
from app.schemas.admin import BoosterApplicationResponse
from app.schemas.booster_service import BoosterServiceResponse
from app.schemas.user import BoosterProfileResponse
from app.services.credit_service import get_credit_service
from app.services.user_service import get_user_service

router = APIRouter(prefix="/users", tags=["users"])


def _map_application_response(user: User) -> BoosterApplicationResponse:
    return BoosterApplicationResponse(
        user_id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        status=user.booster_application_status,
        game_name=user.booster_application_game,
        current_rank=user.booster_application_current_rank,
        target_rank=user.booster_application_target_rank,
        proof_url=user.booster_application_proof_url,
        note=user.booster_application_note,
        booster_quota=user.booster_quota,
        reviewed_by_admin_id=user.reviewed_by_admin_id,
        reviewed_at=user.reviewed_at,
        review_note=user.review_note,
    )


@router.get("/me/booster-application", response_model=BoosterApplicationResponse)
async def get_my_booster_application(current_user: CurrentUser) -> BoosterApplicationResponse:
    return _map_application_response(current_user)


@router.post("/booster-application", response_model=BoosterApplicationResponse)
async def submit_booster_application(
    db: DatabaseSession,
    current_user: CurrentUser,
    game_name: str = Form(..., min_length=1, max_length=100),
    current_rank: str = Form(..., min_length=1, max_length=50),
    target_rank: str = Form(..., min_length=1, max_length=50),
    note: str | None = Form(default=None, max_length=500),
    proof_image: UploadFile = File(...),
) -> BoosterApplicationResponse:
    if current_user.booster_application_status == BoosterApplicationStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="你的代练申请已通过",
        )

    allowed_extensions = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    allowed_content_types = {"image/png", "image/jpeg", "image/webp", "image/gif"}
    max_size_bytes = 5 * 1024 * 1024

    if proof_image.content_type not in allowed_content_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="证明材料必须是图片 (png/jpg/webp/gif)",
        )

    raw_suffix = Path(proof_image.filename or "").suffix.lower()
    if raw_suffix not in allowed_extensions:
        content_suffix_map = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }
        raw_suffix = content_suffix_map.get(proof_image.content_type, "")
    if raw_suffix not in allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="图片格式不支持",
        )

    image_bytes = await proof_image.read(max_size_bytes + 1)
    if len(image_bytes) > max_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="图片过大，限制 5MB",
        )
    if not image_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="图片内容为空",
        )

    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"booster-proof-{current_user.id}-{uuid4().hex}{raw_suffix}"
    file_path = upload_dir / file_name
    file_path.write_bytes(image_bytes)
    proof_url = f"/uploads/{file_name}"

    user_service = get_user_service(db)
    updated_user = await user_service.submit_booster_application(
        user=current_user,
        game_name=game_name,
        current_rank=current_rank,
        target_rank=target_rank,
        proof_url=proof_url,
        note=note,
    )
    return _map_application_response(updated_user)


@router.get(
    "/{user_id}/profile",
    response_model=BoosterProfileResponse,
    summary="获取代练主页",
    description="获取代练的公开信息、信誉数据和标签",
)
async def get_booster_profile(
    user_id: int,
    db: DatabaseSession,
) -> BoosterProfileResponse:
    """Public endpoint: fetch a booster's profile with reputation data."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None or user.role not in (UserRole.BOOSTER, UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="代练不存在",
        )

    return BoosterProfileResponse.model_validate(user)


@router.post(
    "/{user_id}/recalculate-credit",
    response_model=BoosterProfileResponse,
    summary="重算信誉分",
    description="管理员触发重新计算代练信誉数据",
)
async def recalculate_credit(
    user_id: int,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> BoosterProfileResponse:
    """Admin-only: recalculate a booster's credit score."""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有管理员可以触发重算",
        )

    credit_service = get_credit_service(db)
    user = await credit_service.recalculate(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="代练不存在",
        )

    return BoosterProfileResponse.model_validate(user)


@router.get(
    "/{user_id}/services",
    response_model=list[BoosterServiceResponse],
    summary="获取代练的服务列表",
    description="获取指定代练发布的所有可用服务",
)
async def get_booster_services(
    user_id: int,
    db: DatabaseSession,
) -> list[BoosterServiceResponse]:
    """Public endpoint: list all available services by a booster."""
    result = await db.execute(
        select(BoosterService).where(
            BoosterService.booster_id == user_id,
            BoosterService.is_available.is_(True),
        )
    )
    services = list(result.scalars().all())
    return [BoosterServiceResponse.model_validate(s) for s in services]
