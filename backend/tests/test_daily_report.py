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

    async def list_by_date_range(self, start, end, *, service=None):
        return [
            (i, a)
            for i, a in self._rows
            if start <= i.created_at < end and (service is None or i.service == service)
        ]


class FakeChatModel:
    def __init__(self):
        self.last_prompt = None

    async def complete(self, system, user, *, tier="main"):
        self.last_prompt = (system, user, tier)
        return "  *2 incidents today* — GCM critical, resolved.  "


class FakeReportsRepo:
    """Always a cache miss unless seeded — exercises the "generate fresh" path by default."""

    def __init__(self, seed=None):
        self._store = seed or {}
        self.saved = []

    async def get(self, report_date, service):
        return self._store.get((report_date, service))

    async def save(self, service, result):
        self.saved.append((service, result))
        self._store[(result.report_date, service)] = result


class FakeUnitOfWork:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


def _daily_report(incidents, chat, reports=None) -> DailyReport:
    return DailyReport(
        incidents=incidents, chat=chat, reports=reports or FakeReportsRepo(), uow=FakeUnitOfWork()
    )


async def test_generate_aggregates_counts_and_calls_chat():
    d = date(2026, 7, 25)
    i1 = _incident("GCM", "resolved", datetime(2026, 7, 25, 10, tzinfo=timezone.utc))
    i2 = _incident("GCM", "ticketed", datetime(2026, 7, 25, 14, tzinfo=timezone.utc))
    a1 = _analysis(i1.id, "critical", "OOM")
    a2 = _analysis(i2.id, "warning", "slow queries")
    repo = FakeIncidentRepo([(i1, a1), (i2, a2)])
    chat = FakeChatModel()

    result = await _daily_report(repo, chat).generate(d)

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

    result = await _daily_report(repo, FakeChatModel()).generate(d)

    assert len(result.incidents) == 1
    assert result.incidents[0].id == str(in_range.id)


async def test_generate_with_no_incidents_skips_chat_and_uses_fixed_message():
    repo = FakeIncidentRepo([])
    chat = FakeChatModel()

    result = await _daily_report(repo, chat).generate(date(2026, 7, 25))

    assert result.incidents == []
    assert result.counts_by_severity == {}
    assert result.slack_markdown == "No new issues today, have a good day!"
    assert chat.last_prompt is None


async def test_generate_with_no_incidents_for_a_service_names_it_in_the_message():
    repo = FakeIncidentRepo([])
    chat = FakeChatModel()

    result = await _daily_report(repo, chat).generate(date(2026, 7, 25), service="gcm")

    assert result.slack_markdown == "For GCM no new issue today have a good day!"
    assert chat.last_prompt is None


async def test_generate_filters_by_service():
    d = date(2026, 7, 25)
    gcm = _incident("GCM", "new", datetime(2026, 7, 25, 10, tzinfo=timezone.utc))
    evp = _incident("EVP", "new", datetime(2026, 7, 25, 11, tzinfo=timezone.utc))
    repo = FakeIncidentRepo([(gcm, None), (evp, None)])

    result = await _daily_report(repo, FakeChatModel()).generate(d, service="GCM")

    assert len(result.incidents) == 1
    assert result.incidents[0].id == str(gcm.id)


async def test_generate_saves_the_result_for_next_time():
    d = date(2026, 7, 25)
    i1 = _incident("GCM", "resolved", datetime(2026, 7, 25, 10, tzinfo=timezone.utc))
    repo = FakeIncidentRepo([(i1, None)])
    reports = FakeReportsRepo()

    result = await _daily_report(repo, FakeChatModel(), reports).generate(d)

    assert reports.saved == [(None, result)]


async def test_generate_reloads_a_saved_report_instead_of_recomputing():
    d = date(2026, 7, 25)
    # An incident that would change the result if the LLM were actually called again.
    repo = FakeIncidentRepo([(_incident("GCM", "new", datetime(2026, 7, 25, 10, tzinfo=timezone.utc)), None)])
    chat = FakeChatModel()
    reports = FakeReportsRepo()
    await _daily_report(repo, chat, reports).generate(d)
    assert chat.last_prompt is not None
    chat.last_prompt = None  # reset to prove the second call doesn't touch the chat model

    result = await _daily_report(repo, chat, reports).generate(d)

    assert chat.last_prompt is None
    assert len(result.incidents) == 1


async def test_generate_force_recomputes_even_when_a_report_is_saved():
    d = date(2026, 7, 25)
    repo = FakeIncidentRepo([(_incident("GCM", "new", datetime(2026, 7, 25, 10, tzinfo=timezone.utc)), None)])
    chat = FakeChatModel()
    reports = FakeReportsRepo()
    await _daily_report(repo, chat, reports).generate(d)
    chat.last_prompt = None

    await _daily_report(repo, chat, reports).generate(d, force=True)

    assert chat.last_prompt is not None


async def test_todays_report_is_reloaded_on_a_second_visit_just_like_any_other_day():
    """Regression: an earlier version never reused a saved report for today's date, on the theory
    that today "isn't over yet" — but the Reports page auto-loads on every visit (Reports.tsx), so
    that made every visit to today's report a real ~20s LLM call. A new incident logged later the
    same day only shows up once the user explicitly clicks "Regenerate report" (`force=True`),
    same as for a past day."""
    today = date(2026, 9, 11)
    repo = FakeIncidentRepo([(_incident("GCM", "new", datetime(2026, 9, 11, 9, tzinfo=timezone.utc)), None)])
    chat = FakeChatModel()
    reports = FakeReportsRepo()
    await _daily_report(repo, chat, reports).generate(today)
    assert chat.last_prompt is not None
    chat.last_prompt = None

    # A second incident lands later the same day — an unforced re-visit still shows the saved copy.
    repo._rows.append((_incident("GCM", "new", datetime(2026, 9, 11, 14, tzinfo=timezone.utc)), None))
    result = await _daily_report(repo, chat, reports).generate(today)

    assert chat.last_prompt is None  # reloaded, not regenerated
    assert len(result.incidents) == 1  # the saved copy, from before the second incident landed

    # Explicitly regenerating picks up the new incident.
    result = await _daily_report(repo, chat, reports).generate(today, force=True)
    assert chat.last_prompt is not None
    assert len(result.incidents) == 2


async def test_a_past_days_report_is_reloaded_on_a_later_visit():
    yesterday = date(2026, 9, 10)
    repo = FakeIncidentRepo([(_incident("GCM", "new", datetime(2026, 9, 10, 9, tzinfo=timezone.utc)), None)])
    chat = FakeChatModel()
    reports = FakeReportsRepo()
    await _daily_report(repo, chat, reports).generate(yesterday)
    chat.last_prompt = None

    await _daily_report(repo, chat, reports).generate(yesterday)

    assert chat.last_prompt is None  # reloaded, not regenerated


async def test_saved_reports_for_different_projects_do_not_collide():
    d = date(2026, 7, 25)
    reports = FakeReportsRepo()
    await _daily_report(FakeIncidentRepo([]), FakeChatModel(), reports).generate(d, service="gcm")
    await _daily_report(FakeIncidentRepo([]), FakeChatModel(), reports).generate(d)  # all projects

    assert {service for service, _ in reports.saved} == {"gcm", None}
