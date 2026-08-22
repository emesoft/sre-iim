"""Unit tests for the IngestIncident use case using in-memory fakes — no DB, no network.

Because the use case depends only on domain ports, the cache-first behavior (MISS -> HIT -> expiry ->
new-deploy MISS) is fully testable with fakes. This is the core M2 contract.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.application.incidents.ingest import IngestIncident
from app.domain.incidents.entities import Analysis, AnalysisDraft, Incident

pytestmark = pytest.mark.asyncio

_CTX = {
    "service": "GCM",
    "sample_logs": [{"message": "java.lang.OutOfMemoryError: Java heap space"}],
    "recent_deploy": {"version": "1.8.0"},
}


class FakeIncidentRepo:
    def __init__(self):
        self.incidents: dict[uuid.UUID, Incident] = {}
        self.analyses: dict[uuid.UUID, Analysis] = {}

    async def add(self, incident: Incident) -> Incident:
        incident.id = uuid.uuid4()
        incident.created_at = incident.updated_at = datetime(2026, 7, 19, tzinfo=timezone.utc)
        self.incidents[incident.id] = incident
        return incident

    async def get(self, incident_id):
        return self.incidents.get(incident_id)

    async def list(self, **_):
        return []

    async def add_analysis(self, analysis: Analysis) -> Analysis:
        analysis.id = uuid.uuid4()
        analysis.created_at = datetime(2026, 7, 19, tzinfo=timezone.utc)
        self.analyses[analysis.id] = analysis
        return analysis

    async def latest_analysis(self, incident_id):
        found = [a for a in self.analyses.values() if a.incident_id == incident_id]
        return found[-1] if found else None

    async def set_status(self, incident_id, status, *, error_message=None):
        self.incidents[incident_id].status = status
        self.incidents[incident_id].error_message = error_message

    async def update_context(self, incident_id, *, context, fingerprint, log_group=None):
        incident = self.incidents[incident_id]
        incident.context = context
        incident.fingerprint = fingerprint
        if log_group is not None:
            incident.log_group = log_group


class FakeCacheRepo:
    def __init__(self, analyses: dict):
        self._analyses = analyses
        self.entries: dict[str, tuple[uuid.UUID, datetime]] = {}

    async def get_valid(self, fingerprint, now):
        entry = self.entries.get(fingerprint)
        if entry is None or entry[1] <= now:
            return None
        return self._analyses[entry[0]]

    async def put(self, fingerprint, analysis_id, expires_at):
        self.entries[fingerprint] = (analysis_id, expires_at)


class CountingAnalyzer:
    def __init__(self):
        self.calls = 0
        self.last_reporter = None

    async def analyze(self, context, reporter=None):
        self.calls += 1
        self.last_reporter = reporter
        return AnalysisDraft(
            severity="critical",
            summary="GCM OOM after deploy",
            root_cause="heap regression",
            recommended_action="roll back",
            confidence="high",
            model_id="test-model",
        )


class MutableClock:
    def __init__(self, now):
        self._now = now

    def now(self):
        return self._now

    def advance(self, **kw):
        self._now = self._now + timedelta(**kw)


class FakeUnitOfWork:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


def _make(ttl=1800):
    repo = FakeIncidentRepo()
    cache = FakeCacheRepo(repo.analyses)
    analyzer = CountingAnalyzer()
    clock = MutableClock(datetime(2026, 7, 19, 10, 0, tzinfo=timezone.utc))
    uow = FakeUnitOfWork()
    usecase = IngestIncident(
        incidents=repo, cache=cache, analyzer=analyzer, clock=clock, uow=uow, cache_ttl_seconds=ttl
    )
    return usecase, repo, cache, analyzer, clock, uow


async def test_miss_persists_and_normalizes_confidence():
    usecase, repo, _, analyzer, _, uow = _make()
    incident, analysis = await usecase.execute(source="manual", context=dict(_CTX))
    assert analyzer.calls == 1
    assert analysis.cache_state == "MISS"
    assert analysis.confidence == pytest.approx(0.9)  # "high" -> score
    assert incident.status == "analyzed"
    assert analysis.incident_id == incident.id
    assert uow.commits == 2  # one for create_incident, one for analyze_incident
    assert len(repo.incidents) == 1


async def test_create_incident_persists_as_analyzing_without_running_the_analyzer():
    usecase, repo, _, analyzer, _, uow = _make()
    incident = await usecase.create_incident(source="manual", context=dict(_CTX))
    assert incident.status == "analyzing"
    assert incident.id in repo.incidents
    assert analyzer.calls == 0
    assert uow.commits == 1


async def test_create_incident_accepts_an_explicit_status():
    # CloudWatch-alarm-created incidents pass status="new" so they wait for a manual "Analyze
    # with AI" trigger instead of auto-analyzing (see PollAlarmsJob).
    usecase, repo, _, analyzer, _, uow = _make()
    incident = await usecase.create_incident(source="cloudwatch_alarm", context=dict(_CTX), status="new")
    assert incident.status == "new"
    assert repo.incidents[incident.id].status == "new"
    assert analyzer.calls == 0


async def test_analyze_incident_runs_analyzer_and_marks_analyzed():
    usecase, _, _, analyzer, _, uow = _make()
    incident = await usecase.create_incident(source="manual", context=dict(_CTX))
    analysis = await usecase.analyze_incident(incident)
    assert analyzer.calls == 1
    assert analysis.cache_state == "MISS"
    assert incident.status == "analyzed"
    assert uow.commits == 2


async def test_analyze_incident_passes_the_reporter_to_the_analyzer():
    usecase, _, _, analyzer, _, _ = _make()
    incident = await usecase.create_incident(source="manual", context=dict(_CTX))
    reporter = object()
    await usecase.analyze_incident(incident, reporter=reporter)
    assert analyzer.last_reporter is reporter


async def test_cache_hit_reports_a_cached_stage_without_calling_the_analyzer():
    usecase, _, _, analyzer, _, _ = _make()
    await usecase.execute(source="manual", context=dict(_CTX))  # seeds the cache

    class _RecordingReporter:
        def __init__(self):
            self.calls = []

        async def stage(self, name, detail=None):
            self.calls.append((name, detail))

    reporter = _RecordingReporter()
    incident = await usecase.create_incident(source="manual", context=dict(_CTX))
    analysis = await usecase.analyze_incident(incident, reporter=reporter)

    assert analysis.cache_state == "HIT"
    assert analyzer.calls == 1  # still just the original seeding call
    assert reporter.calls == [("cached", None)]


async def test_second_same_context_is_cache_hit_without_llm():
    usecase, _, _, analyzer, _, _ = _make()
    await usecase.execute(source="manual", context=dict(_CTX))
    _, analysis2 = await usecase.execute(source="manual", context=dict(_CTX))
    assert analyzer.calls == 1  # no new LLM call
    assert analysis2.cache_state == "HIT"
    assert analysis2.summary == "GCM OOM after deploy"


async def test_expired_cache_forces_reanalyze():
    usecase, _, _, analyzer, clock, _ = _make(ttl=1800)
    await usecase.execute(source="manual", context=dict(_CTX))
    clock.advance(seconds=1801)
    _, analysis = await usecase.execute(source="manual", context=dict(_CTX))
    assert analyzer.calls == 2
    assert analysis.cache_state == "MISS"


async def test_new_deploy_version_is_a_fresh_miss():
    usecase, _, _, analyzer, _, _ = _make()
    await usecase.execute(source="manual", context=dict(_CTX))
    _, analysis = await usecase.execute(
        source="manual", context={**_CTX, "recent_deploy": {"version": "1.9.0"}}
    )
    assert analyzer.calls == 2
    assert analysis.cache_state == "MISS"


async def test_reanalyze_with_context_updates_fingerprint_and_reruns_analyzer():
    usecase, repo, _, analyzer, _, _uow = _make()
    incident = await usecase.create_incident(source="manual", context=dict(_CTX))
    await usecase.analyze_incident(incident)
    assert analyzer.calls == 1

    new_context = {**_CTX, "sample_logs": [{"message": "different real log line from CloudWatch"}]}
    analysis = await usecase.reanalyze_with_context(
        incident, context=new_context, log_group="/ecs/prod-storefront-logs"
    )

    assert analyzer.calls == 2  # different context -> different fingerprint -> fresh MISS
    assert analysis.cache_state == "MISS"
    assert incident.context == new_context
    assert incident.log_group == "/ecs/prod-storefront-logs"
    assert repo.incidents[incident.id].log_group == "/ecs/prod-storefront-logs"
