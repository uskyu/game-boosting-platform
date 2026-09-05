from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DatabaseSession
from app.schemas.order_template import OrderTemplateCreate, OrderTemplateResponse, OrderTemplateUpdate
from app.services import order_template_service

router = APIRouter(prefix="/order-templates", tags=["order-templates"])


@router.get("", response_model=list[OrderTemplateResponse])
async def list_order_templates(current_user: CurrentUser, db: DatabaseSession):
    return await order_template_service.list_templates(db, current_user.id)


@router.post("", response_model=OrderTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_order_template(data: OrderTemplateCreate, current_user: CurrentUser, db: DatabaseSession):
    try:
        return await order_template_service.create_template(db, current_user.id, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{template_id}", response_model=OrderTemplateResponse)
async def get_order_template(template_id: int, current_user: CurrentUser, db: DatabaseSession):
    template = await order_template_service.get_template(db, current_user.id, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="订单模板不存在")
    return template


@router.patch("/{template_id}", response_model=OrderTemplateResponse)
async def update_order_template(template_id: int, data: OrderTemplateUpdate, current_user: CurrentUser, db: DatabaseSession):
    template = await order_template_service.get_template(db, current_user.id, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="订单模板不存在")
    try:
        return await order_template_service.update_template(db, template, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_order_template(template_id: int, current_user: CurrentUser, db: DatabaseSession):
    template = await order_template_service.get_template(db, current_user.id, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="订单模板不存在")
    await order_template_service.delete_template(db, template)
