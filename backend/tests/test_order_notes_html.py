"""订单备注 HTML 支持（与公告同一套清洗）。

覆盖 2026-10-11 改动：

1. 写入清洗：POST /orders/create 与 PUT /orders/{id} 的 notes 入参过
   sanitize_note_html——script/iframe/onerror/javascript: 被剥掉，
   排版 HTML（b/br/a/img）保留；
2. 读取清洗：OrderResponse.notes 出参再清洗一次——老数据里的纯文本
   （含 <、&）被转义，前端 v-html 不会把老备注当成标签执行；
3. 幂等：sanitize(sanitize(x)) == sanitize(x)，写读两次与只读一次一致；
4. 长度上限仍为 1000（超限 422，行为不变）；
5. 管理员/争议路径往 notes 前缀拼的纯文本在读取清洗后仍完整。
"""

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order, OrderStatus
from app.models.user import User
from app.services.html_sanitizer import sanitize_note_html
from tests.conftest import auth_header

_XSS_NOTES = (
    "<b>加粗</b><br>第二行"
    '<a href="https://example.com">链接</a>'
    '<img src="https://example.com/pic.png" alt="图">'
    "<script>alert(1)</script>"
    '<iframe src="//evil.example"></iframe>'
    '<img src=x onerror="alert(1)">'
    '<a href="javascript:alert(1)">坏链接</a>'
)


async def _create_order_with_notes(
    client: AsyncClient, user: dict, notes: str | None
) -> AsyncClient:
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "100.00",
            "notes": notes,
        },
        headers=auth_header(user),
    )
    return resp


async def test_create_order_notes_html_sanitized_on_write(
    client: AsyncClient, admin_user: dict
) -> None:
    resp = await _create_order_with_notes(client, admin_user, _XSS_NOTES)
    assert resp.status_code == 201, resp.text
    notes = resp.json()["notes"]
    # 排版 HTML 保留
    assert "<b>加粗</b>" in notes
    assert "<br>" in notes
    assert 'href="https://example.com"' in notes
    assert "<img" in notes and 'alt="图"' in notes
    # 可执行内容剥掉
    lowered = notes.lower()
    assert "script" not in lowered
    assert "iframe" not in lowered
    assert "onerror" not in lowered
    assert "javascript:" not in lowered
    # img 的 src 放行绝对 URL；裸相对地址（src=x）剥掉，避免碎图
    assert 'src="https://example.com/pic.png"' in notes
    assert 'src="x"' not in notes
    # 链接按公告同套规则加固
    assert 'target="_blank"' in notes
    assert 'rel="noopener noreferrer"' in notes


async def test_update_order_notes_html_sanitized_on_write(
    client: AsyncClient, admin_user: dict
) -> None:
    created = await _create_order_with_notes(client, admin_user, "普通备注")
    assert created.status_code == 201, created.text
    order_id = created.json()["id"]

    resp = await client.put(
        f"/orders/{order_id}",
        json={"notes": _XSS_NOTES},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    notes = resp.json()["notes"]
    assert "<b>加粗</b>" in notes
    lowered = notes.lower()
    assert "script" not in lowered
    assert "onerror" not in lowered


async def test_legacy_plain_text_notes_escaped_on_read(
    client: AsyncClient,
    admin_user: dict,
    db_session: AsyncSession,
) -> None:
    """老数据（清洗上线前写入）直插 DB，读取时必须转义，不能被当成标签。"""
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
        notes="价格 < 100 & 包赢，周末在线，a < b 成立",
    )
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    resp = await client.get(
        f"/orders/{order.id}", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200, resp.text
    notes = resp.json()["notes"]
    # 不成标签的 < 与 & 被转义：v-html 渲染后用户看到原文，不会吞字
    assert notes == "价格 &lt; 100 &amp; 包赢，周末在线，a &lt; b 成立"


async def test_legacy_unsanitized_html_notes_escaped_on_read(
    client: AsyncClient,
    admin_user: dict,
    db_session: AsyncSession,
) -> None:
    """老数据里万一存了未清洗的 HTML（含脚本），读取路径兜底剥掉。"""
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
        notes='<b>粗</b><script>alert(1)</script>',
    )
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    resp = await client.get(
        f"/orders/{order.id}", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200, resp.text
    notes = resp.json()["notes"]
    assert notes == "<b>粗</b>"


async def test_admin_and_dispute_prefix_survives_read_sanitize(
    client: AsyncClient,
    admin_user: dict,
    db_session: AsyncSession,
) -> None:
    """争议原因/admin 备注是往 notes 首行拼纯文本，读取清洗后仍完整。"""
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
        notes="争议原因: 打手掉线\n<b>原备注</b>",
    )
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    resp = await client.get(
        f"/orders/{order.id}", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200, resp.text
    notes = resp.json()["notes"]
    assert notes.startswith("争议原因: 打手掉线\n")
    assert "<b>原备注</b>" in notes


async def test_notes_sanitize_is_idempotent() -> None:
    once = sanitize_note_html(_XSS_NOTES)
    twice = sanitize_note_html(once)
    assert once == twice
    assert sanitize_note_html(None) is None
    assert sanitize_note_html("") == ""


async def test_notes_over_max_length_still_rejected(
    client: AsyncClient, admin_user: dict
) -> None:
    resp = await _create_order_with_notes(client, admin_user, "x" * 1001)
    assert resp.status_code == 422, resp.text


async def test_notes_at_max_length_accepted(
    client: AsyncClient, admin_user: dict
) -> None:
    resp = await _create_order_with_notes(client, admin_user, "x" * 1000)
    assert resp.status_code == 201, resp.text
    assert resp.json()["notes"] == "x" * 1000
