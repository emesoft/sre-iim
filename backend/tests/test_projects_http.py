"""End-to-end HTTP tests for /api/projects against real Postgres.

Overrides get_encryptor and require_admin (same pattern as test_ado_connections_http.py, even
though projects have no secrets — kept consistent with the other Settings-CRUD test fixtures).
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.infrastructure.db.orm import AdoConnectionRow, Base, CloudConnectionRow, ProjectRow
from app.interface.http.deps import get_session, require_admin
from app.main import app

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://iim:iim@localhost:5432/iim"
)


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
        # See test_project_repository.py's fixture note: `projects` is shared with
        # cloud_connections/ado_connections across test files, so delete both referencing
        # tables before `projects` itself to avoid a leftover row from another file colliding
        # with (or blocking the delete of) the names this file uses.
        await s.execute(delete(CloudConnectionRow))
        await s.execute(delete(AdoConnectionRow))
        await s.execute(delete(ProjectRow))
        await s.commit()

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[require_admin] = lambda: None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()


async def test_create_and_list(client):
    r = await client.post("/api/projects", json={"name": "EVP"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "EVP"
    assert "id" in body and "created_at" in body

    r = await client.get("/api/projects")
    assert r.status_code == 200
    assert [p["name"] for p in r.json()] == ["EVP"]


async def test_create_blank_name_is_422(client):
    r = await client.post("/api/projects", json={"name": "   "})
    assert r.status_code == 422


async def test_create_duplicate_name_is_409(client):
    await client.post("/api/projects", json={"name": "EVP"})
    r = await client.post("/api/projects", json={"name": "EVP"})
    assert r.status_code == 409


async def test_delete_unreferenced_project(client):
    r = await client.post("/api/projects", json={"name": "EVP"})
    project_id = r.json()["id"]

    r = await client.delete(f"/api/projects/{project_id}")
    assert r.status_code == 204

    r = await client.get("/api/projects")
    assert r.json() == []


async def test_delete_unknown_id_is_404(client):
    r = await client.delete("/api/projects/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


async def test_delete_referenced_project_is_409(client, monkeypatch):
    r = await client.post("/api/projects", json={"name": "EVP"})
    project_id = r.json()["id"]

    r = await client.post(
        "/api/cloud-connections",
        json={
            "project": "EVP", "env": "prod", "region": "us-east-1",
            "auth_type": "sso", "sso_profile_name": "p",
        },
    )
    assert r.status_code == 201, r.text

    r = await client.delete(f"/api/projects/{project_id}")
    assert r.status_code == 409
