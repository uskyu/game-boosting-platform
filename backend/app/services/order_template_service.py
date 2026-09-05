from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order_template import OrderTemplate
from app.schemas.order_template import OrderTemplateCreate, OrderTemplateUpdate

MAX_PAYLOAD_BYTES = 16 * 1024


def _payload(data: dict) -> dict:
    import json
    if len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("模板 JSON 大小不能超过 16KB")
    return data


async def list_templates(db: AsyncSession, user_id: int) -> list[OrderTemplate]:
    result = await db.execute(select(OrderTemplate).where(OrderTemplate.user_id == user_id).order_by(OrderTemplate.id))
    return list(result.scalars().all())


async def create_template(db: AsyncSession, user_id: int, data: OrderTemplateCreate) -> OrderTemplate:
    template = OrderTemplate(user_id=user_id, name=data.name, payload=_payload(data.payload.model_dump(mode="json", exclude_none=True, exclude_unset=True)))
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return template


async def get_template(db: AsyncSession, user_id: int, template_id: int) -> OrderTemplate | None:
    result = await db.execute(select(OrderTemplate).where(OrderTemplate.id == template_id, OrderTemplate.user_id == user_id))
    return result.scalar_one_or_none()


async def update_template(db: AsyncSession, template: OrderTemplate, data: OrderTemplateUpdate) -> OrderTemplate:
    values = data.model_dump(exclude_unset=True)
    if "name" in values:
        template.name = values["name"]
    if "payload" in values and values["payload"] is not None:
        template.payload = _payload(data.payload.model_dump(mode="json"))
    await db.commit()
    await db.refresh(template)
    return template


async def delete_template(db: AsyncSession, template: OrderTemplate) -> None:
    await db.delete(template)
    await db.commit()
