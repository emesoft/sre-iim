"""End-to-end HTTP tests for /api/settings/claude-token against real Postgres.

Overrides get_encryptor with a fixed test key so this suite passes regardless of the ambient
SECRET_ENCRYPTION_KEY env var (same pattern test_cloud_connections_http.py uses). Also overrides
require_admin — the admin-password gate itself is covered by test_admin_gate_http.py; this suite
tests the settings endpoints' own behavior, not the gate.
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.infrastructure.db.orm import AppSettingRow, Base
from app.infrastructure.security.encryptor import Encryptor
from app.interface.http.deps import get_encryptor, get_session, require_admin
from app.main import app

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://iim:iim@localhost:5432/iim"
)
_TEST_ENCRYPTION_KEY = "QMveDxMLB0eSF3PseIEr3fWyV7B0F5Ebk2KGC2JaZJk="


@pytest.fixture()
async def client():
    engine = create_async_engine(_DB_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"Postgres not reachable for HTTP test: {exc}")

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await s.execute(delete(AppSettingRow))
        await s.commit()

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_encryptor] = lambda: Encryptor(_TEST_ENCRYPTION_KEY)
    app.dependency_overrides[require_admin] = lambda: None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()


async def test_claude_token_is_not_set_by_default(client):
    r = await client.get("/api/settings/claude-token")
    assert r.status_code == 200
    assert r.json() == {"is_set": False}


async def test_setting_the_token_makes_is_set_true(client):
    r = await client.put("/api/settings/claude-token", json={"token": "sk-ant-oat-example"})
    assert r.status_code == 204, r.text

    r = await client.get("/api/settings/claude-token")
    assert r.status_code == 200
    assert r.json() == {"is_set": True}


async def test_the_response_never_includes_the_token_value(client):
    await client.put("/api/settings/claude-token", json={"token": "sk-ant-oat-example"})
    r = await client.get("/api/settings/claude-token")
    body = r.text
    assert "sk-ant-oat-example" not in body


async def test_setting_an_empty_token_is_422(client):
    r = await client.put("/api/settings/claude-token", json={"token": ""})
    assert r.status_code == 422
