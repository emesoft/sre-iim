"""Daily report response DTOs — pure serialization schemas."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class ReportIncidentOut(BaseModel):
    id: str
    service: str
    status: str
    severity: str | None
    summary: str | None
    ticket_url: str | None


class DailyReportOut(BaseModel):
    """`GET /api/reports/daily`: one day's incidents rolled up into a Slack-postable digest."""

    report_date: date
    counts_by_severity: dict[str, int]
    counts_by_status: dict[str, int]
    incidents: list[ReportIncidentOut]
    slack_markdown: str
