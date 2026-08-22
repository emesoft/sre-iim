"""End-to-end HTTP tests for the admin login endpoint and the require_admin gate.

Overrides get_settings so this suite doesn't depend on the ambient .env for the admin password/
JWT secret. The "valid token is allowed" test needs a reachable Postgres (the gated endpoint's own
dependency chain still resolves a real DB session) — skipped like the other HTTP suites when none
is reachable.
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.infrastructure.config import Settings, get_settings
from app.infrastructure.db.orm import Base
from app.interface.http.deps import get_session
from app.main import app

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://iim:iim@localhost:5432/iim"
)
_TEST_SETTINGS = Settings(admin_password="letmein", admin_jwt_secret="test-jwt-secret")


@pytest.fixture()
async def client():
    app.dependency_overrides[get_settings] = lambda: _TEST_SETTINGS

    engine = create_async_engine(_DB_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        app.dependency_overrides.clear()
        pytest.skip(f"Postgres not reachable for HTTP test: {exc}")

    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()


async def test_login_with_correct_password_returns_a_token(client):
    r = await client.post("/api/auth/admin-login", json={"password": "letmein"})
    assert r.status_code == 200, r.text
    assert "token" in r.json()


async def test_login_with_wrong_password_is_401(client):
    r = await client.post("/api/auth/admin-login", json={"password": "nope"})
    assert r.status_code == 401


async def test_cloud_connections_without_a_token_is_401(client):
    r = await client.get("/api/cloud-connections")
    assert r.status_code == 401


async def test_settings_without_a_token_is_401(client):
    r = await client.get("/api/settings/claude-token")
    assert r.status_code == 401


async def test_cloud_connections_with_a_valid_token_is_allowed(client):
    login = await client.post("/api/auth/admin-login", json={"password": "letmein"})
    token = login.json()["token"]
    r = await client.get("/api/cloud-connections", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


async def test_cloud_connections_with_a_bogus_token_is_401(client):
    r = await client.get("/api/cloud-connections", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401
