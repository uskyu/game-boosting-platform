"""止血批：notes 清洗从 schema 校验器挪到「序列化出口」的回归测试。

覆盖：
1. 详情响应（GET /orders/{id}）里 XSS payload（含 <img src=x onerror=...>）仍被剥掉；
2. 写入路径（POST /orders/create）清洗行为不变，落库即安全 HTML；
3. 老数据（清洗上线前直插 DB）读取仍被清洗：纯文本 <、& 转义，未清洗 HTML 兜底剥掉；
4. 大厅瘦身列表 slim=true 时 notes 置空、不下发，也不对整页订单白跑清洗。
"""

from datetime import datetime
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order, OrderStatus
from app.models.user import User
from app.schemas.order import OrderResponse
from tests.conftest import auth_header

# 富文本 + 可执行内容混合 payload，末尾的 <img src=x onerror=...> 是老板点名的用例
_XSS_NOTES = (
    "<b>加粗</b><br>第二行"
    "<script>alert(1)</script>"
    '<iframe src="//evil.example"></iframe>'
    '<img src=x onerror="alert(1)">'
    '<a href="javascript:alert(1)">坏链接</a>'
)


async def _insert_legacy_order(
    db_session: AsyncSession, notes: str | None
) -> Order:
    """模拟清洗上线前写入的老数据：绕过 schema，直插 ORM。"""
    result = await db_session.execute(
        select(User).where(User.email == "admin_test@example.com")
    )
    user = result.scalar_one()
    order = Order(
        user_id=user.id,
        game_name="王者荣耀",
        current_rank="钻石",
        target_rank="王者",
        price=Decimal("100.00"),
        status=OrderStatus.PENDING,
        notes=notes,
    )
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)
    return order


def test_notes_sanitized_at_serialization_not_validation() -> None:
    """核心回归：校验阶段不再清洗（消除大厅瘦身的白烧），序列化出口仍清洗。"""
    xss = '<b>粗</b><img src=x onerror="alert(1)"><script>alert(1)</script>'
    resp = OrderResponse(
        id=1,
        user_id=1,
        game_name="王者荣耀",
        current_rank="钻石",
        target_rank="王者",
        price=Decimal("100.00"),
        status="PENDING",
        priority=0,
        created_at=datetime(2024, 1, 1),
        updated_at=datetime(2024, 1, 1),
        notes=xss,
    )
    # model_validate/构造阶段原样保留（AfterValidator 已挪走，无每单清洗开销）
    assert resp.notes == xss
    # 真正下发（序列化）时必须清洗
    dumped = resp.model_dump()
    notes = dumped["notes"]
    assert notes is not None
    lowered = notes.lower()
    assert "<b>粗</b>" in notes
    assert "script" not in lowered
    assert "onerror" not in lowered
    assert "javascript:" not in lowered


async def test_detail_response_strips_xss_payload(
    client: AsyncClient, admin_user: dict, db_session: AsyncSession
) -> None:
    """详情响应出口：老数据里的 XSS 仍被剥掉，富文本排版保留。"""
    order = await _insert_legacy_order(db_session, _XSS_NOTES)

    resp = await client.get(f"/orders/{order.id}", headers=auth_header(admin_user))
    assert resp.status_code == 200, resp.text
    notes = resp.json()["notes"]
    assert "<b>加粗</b>" in notes
    assert "<br>" in notes
    lowered = notes.lower()
    assert "script" not in lowered
    assert "iframe" not in lowered
    assert "onerror" not in lowered
    assert "javascript:" not in lowered
    assert "<img src=x" not in notes


async def test_write_path_still_sanitizes(
    client: AsyncClient, admin_user: dict, db_session: AsyncSession
) -> None:
    """写入路径不变：POST create 的 notes 落库即清洗。"""
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "100.00",
            "notes": _XSS_NOTES,
        },
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201, resp.text
    order_id = resp.json()["id"]

    notes = resp.json()["notes"]
    assert "<b>加粗</b>" in notes
    lowered = notes.lower()
    assert "script" not in lowered
    assert "onerror" not in lowered
    assert "javascript:" not in lowered

    # 落库内容本身已安全（写入清洗），而非只靠读时兜底
    stored = (
        await db_session.execute(select(Order).where(Order.id == order_id))
    ).scalar_one()
    assert stored.notes is not None
    assert "script" not in stored.notes.lower()
    assert "onerror" not in stored.notes.lower()


async def test_legacy_plain_text_escaped_on_read(
    client: AsyncClient, admin_user: dict, db_session: AsyncSession
) -> None:
    """老数据读取仍被清洗：纯文本的 <、& 转义。"""
    order = await _insert_legacy_order(
        db_session, "价格 < 100 & 包赢，周末在线，a < b 成立"
    )

    resp = await client.get(f"/orders/{order.id}", headers=auth_header(admin_user))
    assert resp.status_code == 200, resp.text
    assert resp.json()["notes"] == "价格 &lt; 100 &amp; 包赢，周末在线，a &lt; b 成立"


async def test_legacy_unsanitized_html_stripped_on_read(
    client: AsyncClient, admin_user: dict, db_session: AsyncSession
) -> None:
    """老数据里万一存了未清洗的 HTML，读取出口兜底剥掉脚本。"""
    order = await _insert_legacy_order(
        db_session, '<b>粗</b><script>alert(1)</script>'
    )

    resp = await client.get(f"/orders/{order.id}", headers=auth_header(admin_user))
    assert resp.status_code == 200, resp.text
    assert resp.json()["notes"] == "<b>粗</b>"


async def test_slim_list_drops_notes(
    client: AsyncClient, admin_user: dict, db_session: AsyncSession
) -> None:
    """大厅瘦身：slim=true 时 notes 置空不下发（清洗只剩序列化出口，整页零开销）。"""
    await _insert_legacy_order(db_session, _XSS_NOTES)

    resp = await client.get(
        "/orders/", params={"slim": "true"}, headers=auth_header(admin_user)
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert items, resp.text
    assert all(item["notes"] is None for item in items)
