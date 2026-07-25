"""End-to-end HTTP tests for the incident endpoints against a real Postgres.

Overrides the interface-layer `get_session` and `get_analyzer` dependencies (no Bedrock call), then
drives the real FastAPI app through the interface -> application -> infrastructure layers. Skipped
when no database is reachable.
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.incidents.entities import AnalysisDraft, LogEvent
from app.infrastructure.db.orm import (
    EMBED_DIM,
    AnalysisCacheRow,
    AnalysisRow,
    Base,
    DocChunkRow,
    DocumentRow,
    IncidentRow,
)
from app.interface.http.deps import (
    get_base_analyzer,
    get_embedder,
    get_log_fetcher_factory,
    get_session,
)
from app.main import app
from tests.sse_test_utils import iter_sse

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://iim:iim@localhost:5432/iim"
)

_CTX = {
    "service": "GCM",
    "sample_logs": [{"message": "java.lang.OutOfMemoryError: Java heap space"}],
    "recent_deploy": {"version": "1.8.0"},
}


class _FakeAnalyzer:
    async def analyze(self, context: dict, evidence=None) -> AnalysisDraft:
        return AnalysisDraft(
            severity="critical",
            summary="GCM OOM after deploy",
            root_cause="heap regression in 1.8.0",
            recommended_action="roll back GCM",
            confidence="high",
            model_id="test-model",
        )


class _FakeEmbedder:
    async def embed_documents(self, texts):
        return [[0.1] * EMBED_DIM for _ in texts]

    async def embed_query(self, text):
        return [0.1] * EMBED_DIM


class _FakeLogFetcher:
    async def fetch_logs(self, log_group, start, end, filter_pattern=None):
        return [
            LogEvent(timestamp=start, message="[CRITICAL] medusa-api HTTP 500: GET /admin-portal"),
        ]


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
        await s.execute(delete(AnalysisCacheRow))
        await s.execute(delete(AnalysisRow))
        await s.execute(delete(DocChunkRow))
        await s.execute(delete(DocumentRow))  # may reference incidents.id (known-issue cases)
        await s.execute(delete(IncidentRow))
        await s.commit()

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_base_analyzer] = lambda: _FakeAnalyzer()
    app.dependency_overrides[get_embedder] = lambda: _FakeEmbedder()
    app.dependency_overrides[get_log_fetcher_factory] = lambda: lambda service: _FakeLogFetcher()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()


async def _await_analyzed(client, incident_id: str) -> None:
    """Drain the incident's SSE stream to its terminal event — deterministic, no sleep-polling —
    so assertions on its analysis can run once the background analysis has actually finished."""
    async with client.stream("GET", f"/api/incidents/{incident_id}/stream") as resp:
        events = [e async for e in iter_sse(resp)]
    assert events[-1][0] == "analyzed", events


async def test_post_get_and_cache_hit(client):
    r = await client.post("/api/incidents", json={"source": "manual", "context": _CTX})
    assert r.status_code == 201, r.text
    created = r.json()
    incident_id = created["incident_id"]
    assert created["status"] == "analyzing"
    assert created["stream"] == f"/api/incidents/{incident_id}/stream"
    await _await_analyzed(client, incident_id)

    r = await client.get(f"/api/incidents/{incident_id}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["context"]["service"] == "GCM"
    analysis = detail["analysis"]
    assert analysis["_cache"] == "MISS"
    assert analysis["severity"] == "critical"
    assert analysis["confidence"] == pytest.approx(0.9)
    assert analysis["evidence"] == []

    r2 = await client.post("/api/incidents", json={"source": "manual", "context": _CTX})
    assert r2.status_code == 201
    await _await_analyzed(client, r2.json()["incident_id"])
    r2_detail = await client.get(f"/api/incidents/{r2.json()['incident_id']}")
    assert r2_detail.json()["analysis"]["_cache"] == "HIT"

    r = await client.get("/api/incidents")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2
    assert items[0]["severity"] == "critical"
    assert items[0]["summary"] == "GCM OOM after deploy"

    r = await client.get("/api/incidents", params={"severity": "critical"})
    assert len(r.json()) == 2
    r = await client.get("/api/incidents", params={"severity": "info"})
    assert r.json() == []


async def test_missing_service_returns_422(client):
    r = await client.post("/api/incidents", json={"source": "manual", "context": {}})
    assert r.status_code == 422
    assert r.json()["detail"] == "context.service is required"


async def test_log_search_merges_logs_and_reanalyzes(client):
    r = await client.post("/api/incidents", json={"source": "manual", "context": _CTX})
    incident_id = r.json()["incident_id"]
    await _await_analyzed(client, incident_id)

    r = await client.post(
        f"/api/incidents/{incident_id}/logs/search",
        json={
            "log_group": "/ecs/prod-storefront-logs",
            "start": "2026-07-25T00:00:00Z",
            "end": "2026-07-25T01:00:00Z",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["log_group"] == "/ecs/prod-storefront-logs"
    assert len(body["log_events"]) == 1
    assert "500" in body["log_events"][0]["message"]
    assert body["analysis"]["severity"] == "critical"

    detail = (await client.get(f"/api/incidents/{incident_id}")).json()
    assert detail["log_group"] == "/ecs/prod-storefront-logs"
    assert detail["context"]["sample_logs"][0]["message"] == body["log_events"][0]["message"]


async def test_resolve_saves_case_and_next_similar_incident_flags_known_issue(client):
    r1 = await client.post("/api/incidents", json={"source": "manual", "context": _CTX})
    incident1_id = r1.json()["incident_id"]
    await _await_analyzed(client, incident1_id)

    resolve = await client.post(
        f"/api/incidents/{incident1_id}/resolve",
        json={"resolution_notes": "Rolled back to 1.7.9 and bumped container heap size."},
    )
    assert resolve.status_code == 200, resolve.text
    assert resolve.json()["status"] == "resolved"

    other_ctx = {**_CTX, "sample_logs": [{"message": "java.lang.OutOfMemoryError: different trace"}]}
    r2 = await client.post("/api/incidents", json={"source": "manual", "context": other_ctx})
    incident2_id = r2.json()["incident_id"]
    await _await_analyzed(client, incident2_id)

    detail2 = (await client.get(f"/api/incidents/{incident2_id}")).json()
    known_issue = detail2["analysis"]["known_issue"]
    assert known_issue is not None
    assert known_issue["incident_id"] == incident1_id
    assert known_issue["similarity"] == pytest.approx(1.0, abs=1e-6)


async def test_resolve_404_for_unknown_incident(client):
    r = await client.post(
        "/api/incidents/00000000-0000-0000-0000-000000000000/resolve",
        json={"resolution_notes": "n/a"},
    )
    assert r.status_code == 404


async def test_log_search_404_for_unknown_incident(client):
    r = await client.post(
        "/api/incidents/00000000-0000-0000-0000-000000000000/logs/search",
        json={
            "log_group": "/ecs/prod-storefront-logs",
            "start": "2026-07-25T00:00:00Z",
            "end": "2026-07-25T01:00:00Z",
        },
    )
    assert r.status_code == 404
