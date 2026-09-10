"""End-to-end tests for async incident ingest + SSE progress streaming (SSE streaming design,
decision 2026-07-20): POST returns immediately with status="analyzing", analysis runs in the
background, and GET .../stream reports live stage events, a terminal snapshot for an
already-finished incident, and the failed/interrupted fallbacks. Against a real Postgres;
skipped when no database is reachable.
"""

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.incidents.entities import AnalysisDraft, Incident
from app.domain.users.entities import User
from app.infrastructure.db.orm import (
    EMBED_DIM,
    AnalysisCacheRow,
    AnalysisRow,
    Base,
    DocChunkRow,
    DocumentRow,
    IncidentRow,
)
from app.infrastructure.db.repositories import SqlAlchemyIncidentRepository, SqlAlchemyUnitOfWork
from app.interface.http.deps import get_base_analyzer, get_current_user, get_embedder, get_session
from app.main import app
from tests.sse_test_utils import iter_sse

# incidents.py is gated (require_role) now that per-user auth exists — override get_current_user
# with a fake admin so this suite exercises the ingest/stream flow, not the role gate.
_ADMIN_USER = User(
    id=uuid.uuid4(),
    username="admin",
    email="admin@test.local",
    password_hash="unused",
    role="admin",
    created_at=datetime.now(timezone.utc),
)

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://iim:iim@localhost:5432/iim"
)

_CTX = {
    "service": "GCM",
    "sample_logs": [{"message": "java.lang.OutOfMemoryError: Java heap space"}],
    "recent_deploy": {"version": "1.8.0"},
}


class _PausableAnalyzer:
    """A base analyzer that blocks until the test lets it proceed, so tests can observe stage
    events arriving live, before the terminal event, instead of only after the fact."""

    def __init__(self):
        self.started = asyncio.Event()
        self.proceed = asyncio.Event()

    async def analyze(self, context, evidence=None):
        self.started.set()
        await self.proceed.wait()
        return AnalysisDraft(
            severity="critical",
            summary="GCM OOM after deploy",
            root_cause="heap regression",
            recommended_action="roll back",
            confidence="high",
            model_id="test-model",
        )


class _InstantAnalyzer:
    async def analyze(self, context, evidence=None):
        return AnalysisDraft(
            severity="critical",
            summary="GCM OOM after deploy",
            root_cause="heap regression",
            recommended_action="roll back",
            confidence="high",
            model_id="test-model",
        )


class _FailingAnalyzer:
    async def analyze(self, context, evidence=None):
        raise RuntimeError("bedrock unavailable")


class _FakeEmbedder:
    async def embed_documents(self, texts):
        return [[0.1] * EMBED_DIM for _ in texts]

    async def embed_query(self, text):
        return [0.1] * EMBED_DIM


@pytest.fixture()
async def db():
    engine = create_async_engine(_DB_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"Postgres not reachable for streaming test: {exc}")

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await s.execute(delete(AnalysisCacheRow))
        await s.execute(delete(AnalysisRow))
        await s.execute(delete(DocChunkRow))
        await s.execute(delete(DocumentRow))  # may reference incidents.id (known-issue cases)
        await s.execute(delete(IncidentRow))
        await s.commit()

    yield maker
    await engine.dispose()


def _client_for(db, analyzer):
    maker = db

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_base_analyzer] = lambda: analyzer
    app.dependency_overrides[get_embedder] = lambda: _FakeEmbedder()
    app.dependency_overrides[get_current_user] = lambda: _ADMIN_USER
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_post_returns_immediately_with_analyzing_status(db):
    analyzer = _PausableAnalyzer()
    async with _client_for(db, analyzer) as client:
        r = await client.post("/api/incidents", json={"source": "manual", "context": _CTX})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "analyzing"
        assert body["stream"] == f"/api/incidents/{body['incident_id']}/stream"
        analyzer.proceed.set()  # let the background task finish so it doesn't outlive the client
    app.dependency_overrides.clear()


async def test_stream_reports_stage_events_then_the_final_result(db):
    """httpx's ASGITransport buffers the whole response before returning it to the client, so
    this can't observe events arriving *incrementally over the wire* — that liveness property
    (a subscriber draining a queue as events are published) is covered directly by
    IncidentEventBus's own tests. What this proves end-to-end: the background task genuinely
    runs concurrently with the request — a separate task unblocks the paused analyzer while the
    stream request is in flight, not before it's even made — and the full ordered event sequence
    plus the final payload are correct.
    """
    analyzer = _PausableAnalyzer()
    async with _client_for(db, analyzer) as client:
        r = await client.post("/api/incidents", json={"source": "manual", "context": _CTX})
        incident_id = r.json()["incident_id"]
        assert r.json()["status"] == "analyzing"

        async def _unblock_once_started():
            await asyncio.wait_for(analyzer.started.wait(), timeout=5)
            analyzer.proceed.set()

        unblocker = asyncio.create_task(_unblock_once_started())
        async with client.stream("GET", f"/api/incidents/{incident_id}/stream") as resp:
            events = [e async for e in iter_sse(resp)]
        await asyncio.wait_for(unblocker, timeout=5)

    assert [n for n, _ in events] == ["stage", "stage", "analyzed"]
    assert events[0][1]["stage"] == "retrieve"
    assert events[1][1]["stage"] == "analyze"
    result = events[2][1]
    assert result["status"] == "analyzed"
    assert result["analysis"]["severity"] == "critical"
    assert result["analysis"]["_cache"] == "MISS"
    app.dependency_overrides.clear()


async def test_stream_reports_failed_when_the_analyzer_raises(db):
    async with _client_for(db, _FailingAnalyzer()) as client:
        r = await client.post("/api/incidents", json={"source": "manual", "context": _CTX})
        incident_id = r.json()["incident_id"]

        async with client.stream("GET", f"/api/incidents/{incident_id}/stream") as resp:
            events = [e async for e in iter_sse(resp)]

        assert [n for n, _ in events] == ["stage", "stage", "failed"]
        assert events[-1][1]["message"] == "bedrock unavailable"

        detail = await client.get(f"/api/incidents/{incident_id}")
        assert detail.json()["status"] == "failed"
    app.dependency_overrides.clear()


async def test_stream_snapshots_an_already_analyzed_incident(db):
    async with _client_for(db, _InstantAnalyzer()) as client:
        r = await client.post("/api/incidents", json={"source": "manual", "context": _CTX})
        incident_id = r.json()["incident_id"]

        # First connection: drains the live stream to its terminal event.
        async with client.stream("GET", f"/api/incidents/{incident_id}/stream") as resp:
            first = [e async for e in iter_sse(resp)]
        assert first[-1][0] == "analyzed"

        # Second connection, after the fact: just the terminal snapshot, no stage replay.
        async with client.stream("GET", f"/api/incidents/{incident_id}/stream") as resp2:
            second = [e async for e in iter_sse(resp2)]
        assert [n for n, _ in second] == ["analyzed"]
        assert second[0][1]["analysis"]["severity"] == "critical"
    app.dependency_overrides.clear()


async def test_stream_returns_404_for_an_unknown_incident(db):
    async with _client_for(db, _InstantAnalyzer()) as client:
        r = await client.get("/api/incidents/00000000-0000-0000-0000-000000000000/stream")
        assert r.status_code == 404
    app.dependency_overrides.clear()


async def test_stream_reports_interrupted_when_analyzing_with_no_live_channel(db):
    """Simulates a backend restart: the incident row is committed as "analyzing" but no
    background task ever registered a bus channel for it (the in-memory bus was reset)."""
    maker = db
    async with maker() as s:
        incident = await SqlAlchemyIncidentRepository(s).add(
            Incident(
                service="GCM",
                source="manual",
                fingerprint="fp-interrupted",
                context=_CTX,
                status="analyzing",
            )
        )
        await SqlAlchemyUnitOfWork(s).commit()

    async with _client_for(db, _InstantAnalyzer()) as client:
        async with client.stream(
            "GET", f"/api/incidents/{incident.id}/stream"
        ) as resp:
            events = [e async for e in iter_sse(resp)]
        assert events == [("failed", {"message": "analysis interrupted"})]
    app.dependency_overrides.clear()
