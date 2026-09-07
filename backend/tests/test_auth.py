"""Authentication tests: register + login."""

from httpx import AsyncClient


async def test_register_success(client: AsyncClient, make_captcha):
    resp = await client.post("/auth/register", json={
        "email": "new@example.com",
        "username": "NewUser",
        "password": "StrongPass1",
        **make_captcha(),
    })
    assert resp.status_code in (200, 201)
    data = resp.json()
    assert data["user"]["email"] == "new@example.com"
    assert "access_token" in data


async def test_register_duplicate_email(client: AsyncClient, make_captcha):
    from app.services import captcha_service
    payload = {
        "email": "dup@example.com",
        "username": "First",
        "password": "StrongPass1",
        **make_captcha(),
    }
    resp1 = await client.post("/auth/register", json=payload)
    assert resp1.status_code in (200, 201)

    # captcha is single-use: generate a fresh pair for the second attempt
    captcha_id, _ = captcha_service.create()
    code, _ = captcha_service._store[captcha_id]
    payload["username"] = "Second"
    payload["captcha_id"] = captcha_id
    payload["captcha_code"] = code
    resp2 = await client.post("/auth/register", json=payload)
    assert resp2.status_code == 400


async def test_register_missing_captcha_rejected(client: AsyncClient):
    """Register without captcha fields -> 422 (pydantic required fields)."""
    resp = await client.post("/auth/register", json={
        "email": "nocaptcha@example.com",
        "username": "NoCaptcha",
        "password": "StrongPass1",
    })
    assert resp.status_code == 422


async def test_register_validation_error_returns_specific_reason(client: AsyncClient):
    """422 errors carry concrete Chinese reasons (field label + limit)."""
    resp = await client.post("/auth/register", json={
        "email": "short@example.com",
        "username": "王",
        "password": "StrongPass1",
        "captcha_id": "x",
        "captcha_code": "y",
    })
    assert resp.status_code == 422
    errors = resp.json()["errors"]
    assert any("昵称至少需要 2 个字符" in e["message"] for e in errors)

    # 弱密码走 schema 自定义校验器，中文信息须原样透传
    resp2 = await client.post("/auth/register", json={
        "email": "short@example.com",
        "username": "玩家小明",
        "password": "NoDigitsHere",
        "captcha_id": "x",
        "captcha_code": "y",
    })
    assert resp2.status_code == 422
    errors2 = resp2.json()["errors"]
    assert any("密码须包含至少一个数字" in e["message"] for e in errors2)


async def test_register_wrong_captcha_rejected(client: AsyncClient, make_captcha):
    """Register with wrong captcha code -> 400."""
    payload = {
        "email": "badcaptcha@example.com",
        "username": "BadCaptcha",
        "password": "StrongPass1",
        **make_captcha(),
    }
    payload["captcha_code"] = "WRONG"
    resp = await client.post("/auth/register", json=payload)
    assert resp.status_code == 400


async def test_register_weak_password(client: AsyncClient, make_captcha):
    resp = await client.post("/auth/register", json={
        "email": "weak@example.com",
        "username": "WeakUser",
        "password": "nodigits",
        **make_captcha(),
    })
    assert resp.status_code == 422


async def test_login_success(client: AsyncClient, make_captcha):
    # Register first
    await client.post("/auth/register", json={
        "email": "login@example.com",
        "username": "LoginUser",
        "password": "LoginPass1",
        **make_captcha(),
    })

    resp = await client.post("/auth/login", json={
        "email": "login@example.com",
        "password": "LoginPass1",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data


async def test_login_wrong_password(client: AsyncClient, make_captcha):
    await client.post("/auth/register", json={
        "email": "wrongpw@example.com",
        "username": "WrongPw",
        "password": "CorrectPass1",
        **make_captcha(),
    })

    resp = await client.post("/auth/login", json={
        "email": "wrongpw@example.com",
        "password": "WrongPassword1",
    })
    assert resp.status_code == 401
