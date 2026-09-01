"""Health and root endpoint tests."""

from httpx import AsyncClient


async def test_health_endpoint(client: AsyncClient):
    resp = await client.get("http://test/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"


async def test_root_endpoint(client: AsyncClient):
    resp = await client.get("http://test/")
    assert resp.status_code == 200
    data = resp.json()
    assert "docs" in data
