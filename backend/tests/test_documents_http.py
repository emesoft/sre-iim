"""End-to-end HTTP tests for /api/documents against real Postgres (fake embedder, no Bedrock).

Skipped when no database is reachable.
"""

import os
import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.users.entities import User
from app.infrastructure.db.orm import EMBED_DIM, Base, DocChunkRow, DocumentRow
from app.interface.http.deps import get_current_user, get_embedder, get_session
from app.main import app

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://iim:iim@localhost:5432/iim"
)
_DIM = EMBED_DIM

# documents.py is gated (require_role) now that per-user auth exists — override get_current_user
# with a fake admin so these tests exercise document ingest/CRUD, not the role gate.
_ADMIN_USER = User(
    id=uuid.uuid4(),
    username="admin",
    email="admin@test.local",
    password_hash="unused",
    role="admin",
    created_at=datetime.now(timezone.utc),
)


class _FakeEmbedder:
    async def embed_documents(self, texts):
        return [[0.1] * _DIM for _ in texts]

    async def embed_query(self, text):
        return [0.1] * _DIM


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
        await s.execute(delete(DocChunkRow))
        await s.execute(delete(DocumentRow))
        await s.commit()

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_embedder] = lambda: _FakeEmbedder()
    app.dependency_overrides[get_current_user] = lambda: _ADMIN_USER

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()


async def test_ingest_and_list(client):
    body = {
        "title": "GCM OOM Runbook",
        "source_type": "runbook",
        "service": "GCM",
        "tags": ["oom", "memory"],
        "content": "# GCM OOM\n" + "\n".join(f"step {i}: do the thing" for i in range(40)),
    }
    r = await client.post("/api/documents", json=body)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["chunks"] >= 1

    r = await client.get("/api/documents")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["title"] == "GCM OOM Runbook"
    assert items[0]["source_type"] == "runbook"
    assert items[0]["chunk_count"] == created["chunks"]
    assert items[0]["tags"] == ["oom", "memory"]


async def test_invalid_source_type_returns_422(client):
    r = await client.post(
        "/api/documents",
        json={"title": "x", "source_type": "wiki", "content": "hello world"},
    )
    assert r.status_code == 422
    assert "source_type" in r.json()["detail"]


async def test_empty_content_returns_422(client):
    r = await client.post(
        "/api/documents",
        json={"title": "x", "source_type": "runbook", "content": "   \n  "},
    )
    assert r.status_code == 422
