"""Unit tests for the DailyReport use case using in-memory fakes — no DB, no network."""

import uuid
from datetime import date, datetime, timezone

import pytest

from app.application.incidents.daily_report import DailyReport
from app.domain.incidents.entities import Analysis, Incident

pytestmark = pytest.mark.asyncio


def _incident(service: str, status: str, created_at: datetime) -> Incident:
    return Incident(
        service=service,
        source="manual",
        fingerprint="fp",
        context={"service": service},
        status=status,
        id=uuid.uuid4(),
        created_at=created_at,
        updated_at=created_at,
    )


def _analysis(incident_id, severity: str, summary: str) -> Analysis:
    return Analysis(
        incident_id=incident_id,
        severity=severity,
        summary=summary,
        root_cause="r",
        recommended_action="a",
        confidence=0.9,
        cache_state="MISS",
        model_id="test-model",
    )


class FakeIncidentRepo:
    def __init__(self, rows):
        self._rows = rows

    async def list_by_date_range(self, start, end):
        return [(i, a) for i, a in self._rows if start <= i.created_at < end]


class FakeChatModel:
    def __init__(self):
        self.last_prompt = None

    async def complete(self, system, user, *, tier="main"):
        self.last_prompt = (system, user, tier)
        return "  *2 incidents today* — GCM critical, resolved.  "


async def test_generate_aggregates_counts_and_calls_chat():
    d = date(2026, 7, 25)
    i1 = _incident("GCM", "resolved", datetime(2026, 7, 25, 10, tzinfo=timezone.utc))
    i2 = _incident("GCM", "ticketed", datetime(2026, 7, 25, 14, tzinfo=timezone.utc))
    a1 = _analysis(i1.id, "critical", "OOM")
    a2 = _analysis(i2.id, "warning", "slow queries")
    repo = FakeIncidentRepo([(i1, a1), (i2, a2)])
    chat = FakeChatModel()

    result = await DailyReport(incidents=repo, chat=chat).generate(d)

    assert result.report_date == d
    assert result.counts_by_severity == {"critical": 1, "warning": 1}
    assert result.counts_by_status == {"resolved": 1, "ticketed": 1}
    assert len(result.incidents) == 2
    assert result.slack_markdown == "*2 incidents today* — GCM critical, resolved."
    assert chat.last_prompt[2] == "fast"
    assert "GCM" in chat.last_prompt[1]


async def test_generate_excludes_incidents_outside_the_date():
    d = date(2026, 7, 25)
    in_range = _incident("GCM", "new", datetime(2026, 7, 25, 0, 1, tzinfo=timezone.utc))
    out_of_range = _incident("GCM", "new", datetime(2026, 7, 26, 0, 1, tzinfo=timezone.utc))
    repo = FakeIncidentRepo([(in_range, None), (out_of_range, None)])

    result = await DailyReport(incidents=repo, chat=FakeChatModel()).generate(d)

    assert len(result.incidents) == 1
    assert result.incidents[0].id == str(in_range.id)


async def test_generate_with_no_incidents_still_calls_chat():
    repo = FakeIncidentRepo([])
    chat = FakeChatModel()

    result = await DailyReport(incidents=repo, chat=chat).generate(date(2026, 7, 25))

    assert result.incidents == []
    assert result.counts_by_severity == {}
    assert "No incidents" in chat.last_prompt[1]
