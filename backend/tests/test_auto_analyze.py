"""Unit tests for AutoAnalyzeIncidents using in-memory fakes — no DB, no LLM.

What's actually being protected here is spend: automatic analysis must only fire for alarms the
provider itself called urgent, must stay under its per-run cap, and must never pay twice for an
incident it already failed on.
"""

import uuid

import pytest

from app.application.integrations.poll_alarms import _build_alert_context
from app.application.incidents.auto_analyze import AutoAnalyzeIncidents
from app.domain.integrations.entities import ALARMS, AlarmState, Integration
from app.domain.incidents.entities import Incident
from app.infrastructure.cloud.cloudwatch_alarms import priority_from_name


def _incident(priority: str | None = None) -> Incident:
    context: dict = {"service": "GCM"}
    if priority is not None:
        context["priority"] = priority
    return Incident(
        service="GCM", source="cloudwatch_alarm", fingerprint="fp", context=context,
        status="new", id=uuid.uuid4(),
    )


class FakeIncidentRepo:
    def __init__(self, rows: list[Incident]):
        self._rows = rows
        self.statuses: list[tuple[uuid.UUID, str, str | None]] = []
        self.queries: list[tuple[tuple[str, ...], int]] = []

    async def list_pending_auto_analysis(self, *, priorities, limit):
        self.queries.append((tuple(priorities), limit))
        matching = [i for i in self._rows if i.context.get("priority") in priorities]
        return matching[:limit]

    async def set_status(self, incident_id, status, *, error_message=None):
        self.statuses.append((incident_id, status, error_message))


class FakeUnitOfWork:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


class FakeIngest:
    def __init__(self, raises: Exception | None = None):
        self.analyzed: list[uuid.UUID] = []
        self.uow = FakeUnitOfWork()
        self._raises = raises

    async def analyze_incident(self, incident, *, reporter=None):
        self.analyzed.append(incident.id)
        if self._raises is not None:
            raise self._raises
        return object()


async def test_analyzes_only_incidents_whose_alarm_reported_an_urgent_priority():
    critical = _incident("critical")
    high = _incident("high")
    repo = FakeIncidentRepo([critical, _incident("low"), high, _incident(None)])
    ingest = FakeIngest()

    result = await AutoAnalyzeIncidents(incidents=repo, ingest=ingest, limit=10).run()

    assert set(ingest.analyzed) == {critical.id, high.id}
    assert result.analyzed == 2


async def test_an_alarm_with_no_reported_priority_is_never_picked_up():
    """CloudWatch has no priority concept. Treating "unknown" as urgent would mean an LLM call for
    every alarm in the account, so it has to be opted into explicitly."""
    repo = FakeIncidentRepo([_incident(None), _incident(None)])
    ingest = FakeIngest()

    result = await AutoAnalyzeIncidents(incidents=repo, ingest=ingest, limit=10).run()

    assert ingest.analyzed == []
    assert result.analyzed == 0


async def test_unknown_can_be_opted_into():
    unknown = _incident("unknown")
    repo = FakeIncidentRepo([unknown])
    ingest = FakeIngest()

    await AutoAnalyzeIncidents(
        incidents=repo, ingest=ingest, priorities=("critical", "high", "unknown"), limit=10
    ).run()

    assert ingest.analyzed == [unknown.id]


async def test_the_per_run_cap_is_passed_to_the_query_and_honoured():
    repo = FakeIncidentRepo([_incident("critical") for _ in range(10)])
    ingest = FakeIngest()

    result = await AutoAnalyzeIncidents(incidents=repo, ingest=ingest, limit=3).run()

    assert repo.queries == [(("critical", "high"), 3)]
    assert result.analyzed == 3


async def test_limit_zero_turns_the_sweep_off_without_querying():
    repo = FakeIncidentRepo([_incident("critical")])
    ingest = FakeIngest()

    result = await AutoAnalyzeIncidents(incidents=repo, ingest=ingest, limit=0).run()

    assert repo.queries == []
    assert ingest.analyzed == []
    assert result == type(result)(analyzed=0, failed=0)


async def test_a_failing_analysis_marks_the_incident_failed_so_it_is_not_retried_forever():
    """Left as "new", the same incident would come back on every sweep and be paid for again."""
    incident = _incident("critical")
    repo = FakeIncidentRepo([incident])
    ingest = FakeIngest(raises=RuntimeError("provider 429"))

    result = await AutoAnalyzeIncidents(incidents=repo, ingest=ingest, limit=5).run()

    assert result == type(result)(analyzed=0, failed=1)
    assert repo.statuses == [(incident.id, "failed", "provider 429")]
    assert ingest.uow.commits == 1


async def test_one_failure_does_not_stop_the_rest_of_the_sweep():
    class FlakyIngest(FakeIngest):
        async def analyze_incident(self, incident, *, reporter=None):
            self.analyzed.append(incident.id)
            if len(self.analyzed) == 1:
                raise RuntimeError("boom")
            return object()

    first, second = _incident("critical"), _incident("critical")
    repo = FakeIncidentRepo([first, second])
    ingest = FlakyIngest()

    result = await AutoAnalyzeIncidents(incidents=repo, ingest=ingest, limit=5).run()

    assert ingest.analyzed == [first.id, second.id]
    assert result.analyzed == 1 and result.failed == 1


# --- the ingest side: priority has to reach `context` for any of the above to fire -------------


def _connection(provider="aws") -> Integration:
    return Integration(
        project="GCM", env="prod", provider=provider, capabilities=(ALARMS,),
        config={"region": "us-east-1", "auth_type": "sso", "sso_profile_name": "p"},
    )


def test_alarm_priority_is_recorded_in_the_incident_context():
    context = _build_alert_context(
        _connection("newrelic"),
        AlarmState(arn="i:1", name="CRITICAL - Yelp Errors", state="ALARM", priority="critical"),
    )
    assert context["priority"] == "critical"


def test_context_has_no_priority_key_when_the_provider_reported_none():
    context = _build_alert_context(
        _connection(), AlarmState(arn="arn:1", name="cpu-high-ish", state="ALARM")
    )
    assert "priority" not in context


@pytest.mark.parametrize(
    "name,expected",
    [
        ("CRITICAL - Yelp Errors", "critical"),
        ("prod-sev1-db-down", "critical"),
        ("P1 checkout failure", "critical"),
        ("HIGH cpu on api", "high"),
        ("urgent-queue-backlog", "high"),
        # No urgency claimed: "AlarmLow"/"LowECSMemory" are about the metric being low, not about
        # the alarm mattering less, and neither should be read as a priority at all.
        ("ecs-easyrx-prod-external-svc-AlarmLow", None),
        ("redemption-ui-LowECSMemoryAlarm", None),
        ("rds-mysql-freeable-memory-prod", None),
        # Substrings of longer words must not match — "highway", "p10" are not priorities.
        ("highway-latency", None),
        ("p10-latency-alarm", None),
    ],
)
def test_cloudwatch_priority_is_only_read_from_an_explicit_marker_in_the_name(name, expected):
    assert priority_from_name(name) == expected
