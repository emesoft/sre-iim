"""SQLAlchemy implementation of DailyReportRepository (implements the port in
application/incidents/daily_report.py — not a domain port, see that module's docstring).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.incidents.daily_report import DailyReportResult, ReportIncident
from app.infrastructure.db.orm import DailyReportRow

# A real project name is never empty, so this can't collide with one — see DailyReportRow's
# docstring for why the column is a non-null string rather than nullable.
_ALL_PROJECTS = ""


class SqlAlchemyDailyReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, report_date: date, service: str | None) -> DailyReportResult | None:
        row = await self._s.scalar(
            select(DailyReportRow).where(
                DailyReportRow.report_date == report_date,
                DailyReportRow.service == (service or _ALL_PROJECTS),
            )
        )
        if row is None:
            return None
        return DailyReportResult(
            report_date=row.report_date,
            counts_by_severity=row.counts_by_severity,
            counts_by_status=row.counts_by_status,
            incidents=[ReportIncident(**i) for i in row.incidents],
            slack_markdown=row.slack_markdown,
        )

    async def save(self, service: str | None, result: DailyReportResult) -> None:
        key_service = service or _ALL_PROJECTS
        row = await self._s.scalar(
            select(DailyReportRow).where(
                DailyReportRow.report_date == result.report_date,
                DailyReportRow.service == key_service,
            )
        )
        if row is None:
            row = DailyReportRow(report_date=result.report_date, service=key_service)
            self._s.add(row)
        row.counts_by_severity = result.counts_by_severity
        row.counts_by_status = result.counts_by_status
        row.incidents = [asdict(i) for i in result.incidents]
        row.slack_markdown = result.slack_markdown
        await self._s.flush()
