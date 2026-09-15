"""Integration test: IngestIncident wired with the real SQLAlchemy repositories + Postgres.

Proves the domain ports' concrete adapters persist correctly and the cache short-circuits the
analyzer across separate sessions. Skipped when no database is reachable (set TEST_DATABASE_URL).
"""

import os

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.incidents.ingest import IngestIncident
from app.domain.incidents.ports import FixedAnalyzer
from app.domain.incidents.entities import AnalysisDraft
from app.infrastructure.clock import SystemClock
from app.infrastructure.db.orm import AnalysisCacheRow, AnalysisRow, Base, IncidentRow
from app.infrastructure.db.repositories import (
    SqlAlchemyAnalysisCacheRepository,
    SqlAlchemyIncidentRepository,
    SqlAlchemyUnitOfWork,
)

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://iim:iim@localhost:5432/iim"
)

_CTX = {
    "service": "GCM",
    "sample_logs": [{"message": "java.lang.OutOfMemoryError: Java heap space"}],
    "recent_deploy": {"version": "1.8.0"},
    "metrics": {"MemoryUtilization": 98.7},
}


class _CountingAnalyzer:
    def __init__(self):
        self.calls = 0

    async def analyze(self, context: dict, reporter=None) -> AnalysisDraft:
        self.calls += 1
        return AnalysisDraft(
            severity="critical",
            summary="GCM OOM after deploy",
            root_cause="heap regression",
            recommended_action="roll back",
            confidence="high",
            model_id="test-model",
        )


def _usecase(session, analyzer):
    return IngestIncident(
        incidents=SqlAlchemyIncidentRepository(session),
        cache=SqlAlchemyAnalysisCacheRepository(session),
        analyzers=FixedAnalyzer(analyzer),
        enricher=None,
        clock=SystemClock(),
        uow=SqlAlchemyUnitOfWork(session),
        cache_ttl_seconds=1800,
    )


@pytest.fixture()
async def sessionmaker_fixture():
    engine = create_async_engine(_DB_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001 - any setup failure means "no DB available"
        await engine.dispose()
        pytest.skip(f"Postgres not reachable for integration test: {exc}")

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await s.execute(delete(AnalysisCacheRow))
        await s.execute(delete(AnalysisRow))
        await s.execute(delete(IncidentRow))
        await s.commit()
    yield maker
    await engine.dispose()


async def test_miss_then_hit_then_miss_on_new_deploy(sessionmaker_fixture):
    maker = sessionmaker_fixture
    analyzer = _CountingAnalyzer()

    async with maker() as s:
        incident1, analysis1 = await _usecase(s, analyzer).execute(
            source="manual", context=dict(_CTX)
        )
    assert analyzer.calls == 1
    assert analysis1.cache_state == "MISS"
    assert incident1.status == "analyzed"
    assert analysis1.confidence == pytest.approx(0.9)
    assert analysis1.incident_id == incident1.id
    assert incident1.created_at is not None  # server default round-tripped

    async with maker() as s:
        _, analysis2 = await _usecase(s, analyzer).execute(source="manual", context=dict(_CTX))
    assert analyzer.calls == 1  # cache HIT, no new LLM call
    assert analysis2.cache_state == "HIT"
    assert analysis2.summary == analysis1.summary

    async with maker() as s:
        _, analysis3 = await _usecase(s, analyzer).execute(
            source="manual", context={**_CTX, "recent_deploy": {"version": "1.9.0"}}
        )
    assert analyzer.calls == 2
    assert analysis3.cache_state == "MISS"
