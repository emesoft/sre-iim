"""DailyReport use case: roll up one day's incidents into a Slack-postable digest.

Aggregates counts (by severity/status) purely in code, then makes one LLM call — reusing the
generic `ChatModel` port already used by the multi-agent graph nodes — to turn the incident list
into a short narrative digest an SRE can paste into a status channel. Generation only; posting to
Slack is a manual copy/paste in this version (no bot/webhook).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from app.domain.incidents.entities import Analysis, Incident
from app.domain.incidents.ports import IncidentRepository
from app.domain.llm import ChatModel

_SYSTEM_PROMPT = """You are an SRE writing a short end-of-day incident digest for a Slack channel.
Summarize only the incidents given — never invent services, causes, or counts not present in the
data. Use Slack-flavored Markdown (bold with *asterisks*, bullet lists with "-"). Keep it under
200 words. Structure: a one-line headline with the total count and worst severity, then a bullet
per incident (service, severity, one-line status), then a one-line closing note if anything is
still open/unticketed."""


@dataclass(frozen=True)
class ReportIncident:
    id: str
    service: str
    status: str
    severity: str | None
    summary: str | None
    ticket_url: str | None


@dataclass(frozen=True)
class DailyReportResult:
    report_date: date
    counts_by_severity: dict[str, int]
    counts_by_status: dict[str, int]
    incidents: list[ReportIncident] = field(default_factory=list)
    slack_markdown: str = ""


def _day_bounds_utc(report_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(report_date, time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _render_prompt(report_date: date, rows: list[ReportIncident]) -> str:
    if not rows:
        return f"Date: {report_date.isoformat()}\nNo incidents were recorded today."
    lines = [f"Date: {report_date.isoformat()}", f"Incident count: {len(rows)}", ""]
    for r in rows:
        lines.append(
            f"- service={r.service} severity={r.severity or 'n/a'} status={r.status} "
            f"summary={r.summary or 'n/a'} ticket={r.ticket_url or 'none'}"
        )
    return "\n".join(lines)


@dataclass
class DailyReport:
    incidents: IncidentRepository
    chat: ChatModel

    async def generate(self, report_date: date) -> DailyReportResult:
        start, end = _day_bounds_utc(report_date)
        rows = await self.incidents.list_by_date_range(start, end)

        report_rows = [_to_report_incident(inc, an) for inc, an in rows]
        counts_by_severity = Counter(r.severity or "unknown" for r in report_rows)
        counts_by_status = Counter(r.status for r in report_rows)

        markdown = await self.chat.complete(
            _SYSTEM_PROMPT, _render_prompt(report_date, report_rows), tier="fast"
        )

        return DailyReportResult(
            report_date=report_date,
            counts_by_severity=dict(counts_by_severity),
            counts_by_status=dict(counts_by_status),
            incidents=report_rows,
            slack_markdown=markdown.strip(),
        )


def _to_report_incident(incident: Incident, analysis: Analysis | None) -> ReportIncident:
    return ReportIncident(
        id=str(incident.id),
        service=incident.service,
        status=incident.status,
        severity=analysis.severity if analysis else None,
        summary=analysis.summary if analysis else None,
        ticket_url=incident.ticket_url,
    )
