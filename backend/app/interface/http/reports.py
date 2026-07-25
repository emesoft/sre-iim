"""Reports HTTP controller: the daily SRE digest (generate-and-copy-for-Slack, no bot/webhook)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query

from app.application.incidents.daily_report import DailyReport
from app.interface.http.deps import get_daily_report
from app.interface.http.dto.response import DailyReportOut, ReportIncidentOut

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/daily", response_model=DailyReportOut)
async def get_daily_report_view(
    report_date: date = Query(..., alias="date"),
    report: DailyReport = Depends(get_daily_report),
) -> DailyReportOut:
    """Roll up `date`'s incidents into a Slack-postable digest (counts + narrative markdown)."""
    result = await report.generate(report_date)
    return DailyReportOut(
        report_date=result.report_date,
        counts_by_severity=result.counts_by_severity,
        counts_by_status=result.counts_by_status,
        incidents=[
            ReportIncidentOut(
                id=r.id,
                service=r.service,
                status=r.status,
                severity=r.severity,
                summary=r.summary,
                ticket_url=r.ticket_url,
            )
            for r in result.incidents
        ],
        slack_markdown=result.slack_markdown,
    )
