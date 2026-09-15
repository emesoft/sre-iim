"""Unit tests for gathering live evidence before analysis — no AWS, no DB.

The behaviour worth pinning isn't the happy path, it's the guarantees around it: enrichment must
never take an analysis down, must not spend API calls when no LLM call is going to happen, and
must not disturb the cache key it rides along with.
"""

import uuid
from datetime import datetime, timezone

from app.application.incidents.ingest import IngestIncident
from app.domain.incidents.entities import AnalysisDraft, Incident
from app.domain.incidents.ports import FixedAnalyzer


class FakeAnalyzer:
    def __init__(self):
        self.seen: list[dict] = []

    async def analyze(self, context, evidence=None, reporter=None):
        self.seen.append(context)
        return AnalysisDraft(
            severity="medium", summary="s", root_cause="r", recommended_action="a",
            confidence="medium", model_id="fake",
        )


class FakeIncidents:
    def __init__(self):
        self.updates: list[tuple] = []
        self.analyses: list = []

    async def add_analysis(self, analysis):
        self.analyses.append(analysis)
        return analysis

    async def update_context(self, incident_id, *, context, fingerprint, log_group=None):
        self.updates.append((incident_id, context, fingerprint))

    async def set_status(self, incident_id, status, *, error_message=None):
        pass


class FakeCache:
    def __init__(self, hit=None):
        self.hit = hit
        self.put_calls = 0

    async def get_valid(self, fingerprint, now):
        return self.hit

    async def put(self, fingerprint, analysis_id, expires_at):
        self.put_calls += 1


class FakeUow:
    async def commit(self):
        pass

    async def rollback(self):
        pass


class FakeClock:
    def now(self):
        return datetime(2026, 9, 13, tzinfo=timezone.utc)


class RecordingEnricher:
    def __init__(self, extra=None, boom=False):
        self.extra = extra or {}
        self.boom = boom
        self.calls = 0

    async def enrich(self, service, context):
        self.calls += 1
        if self.boom:
            raise RuntimeError("SSO session expired")
        return self.extra


def _incident() -> Incident:
    return Incident(
        id=uuid.uuid4(), service="EVP", source="cloudwatch_alarm", fingerprint="fp-1",
        context={"service": "EVP", "alert": "CloudWatch alarm 'x' is in ALARM"}, status="new",
    )


def _ingest(incidents, cache, analyzer, enricher=None) -> IngestIncident:
    return IngestIncident(
        incidents=incidents, cache=cache, analyzers=FixedAnalyzer(analyzer), clock=FakeClock(),
        uow=FakeUow(), cache_ttl_seconds=600, enricher=enricher,
    )


async def test_evidence_reaches_the_analyzer():
    """The whole point: the model reasons from real task counts, not from the alarm text alone."""
    analyzer = FakeAnalyzer()
    enricher = RecordingEnricher({"ecs": {"desired": 2, "running": 0}})
    await _ingest(FakeIncidents(), FakeCache(), analyzer, enricher).analyze_incident(_incident())
    assert analyzer.seen[0]["ecs"] == {"desired": 2, "running": 0}


async def test_the_enriched_context_is_persisted_so_the_analysis_is_reviewable():
    """An analysis citing numbers nobody else can see isn't reviewable — the detail view has to
    show the same facts the model was given."""
    incidents = FakeIncidents()
    enricher = RecordingEnricher({"ecs": {"running": 0}})
    await _ingest(incidents, FakeCache(), FakeAnalyzer(), enricher).analyze_incident(_incident())
    assert incidents.updates, "enriched context was never written back"
    _, context, _ = incidents.updates[0]
    assert context["ecs"] == {"running": 0}


async def test_the_fingerprint_is_left_alone():
    """It's the cache key for "the same problem". Re-deriving it from live values that move every
    minute would mean the cache never hits again, and every repeat firing pays for a fresh call."""
    incidents = FakeIncidents()
    enricher = RecordingEnricher({"metrics": {"latest": 11.4}})
    incident = _incident()
    await _ingest(incidents, FakeCache(), FakeAnalyzer(), enricher).analyze_incident(incident)
    _, _, fingerprint = incidents.updates[0]
    assert fingerprint == "fp-1"


async def test_a_failing_lookup_does_not_take_the_analysis_down():
    """Credentials expire, SSO sessions lapse, a role is missing one permission. None of that is a
    reason to leave an incident untriaged."""
    analyzer = FakeAnalyzer()
    ingest = _ingest(FakeIncidents(), FakeCache(), analyzer, RecordingEnricher(boom=True))
    analysis = await ingest.analyze_incident(_incident())
    assert analysis.summary == "s"
    assert analyzer.seen, "analysis never ran"


async def test_nothing_found_leaves_the_context_untouched():
    incidents = FakeIncidents()
    await _ingest(incidents, FakeCache(), FakeAnalyzer(), RecordingEnricher({})).analyze_incident(
        _incident()
    )
    assert incidents.updates == []


async def test_a_cache_hit_spends_no_api_calls():
    """No LLM call is happening, so gathering evidence for it would be paying for nothing."""
    cached = _cached_analysis()
    enricher = RecordingEnricher({"ecs": {"running": 0}})
    await _ingest(FakeIncidents(), FakeCache(hit=cached), FakeAnalyzer(), enricher).analyze_incident(
        _incident()
    )
    assert enricher.calls == 0


async def test_the_pipeline_still_works_with_no_enricher_at_all():
    """Every deployment without provider credentials, and every test that doesn't care."""
    analyzer = FakeAnalyzer()
    await _ingest(FakeIncidents(), FakeCache(), analyzer).analyze_incident(_incident())
    assert analyzer.seen


def _cached_analysis():
    from app.domain.incidents.entities import Analysis

    return Analysis(
        incident_id=uuid.uuid4(), severity="low", summary="cached", root_cause="r",
        recommended_action="a", confidence=0.5, cache_state="MISS", model_id="fake",
    )
