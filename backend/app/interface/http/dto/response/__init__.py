"""Outbound response DTOs, one module per resource."""

from app.interface.http.dto.response.cloud_connection import (
    CloudConnectionOut,
    PollResult,
    TestConnectionResult,
)
from app.interface.http.dto.response.document import DocumentCreatedResponse, DocumentSummary
from app.interface.http.dto.response.health import HealthResponse
from app.interface.http.dto.response.incident import (
    AnalysisOut,
    IncidentCreatedResponse,
    IncidentDetail,
    IncidentSummary,
    KnownIssueOut,
    LogEventOut,
    LogSearchResult,
)
from app.interface.http.dto.response.report import DailyReportOut, ReportIncidentOut

__all__ = [
    "IncidentCreatedResponse",
    "AnalysisOut",
    "IncidentSummary",
    "IncidentDetail",
    "KnownIssueOut",
    "LogEventOut",
    "LogSearchResult",
    "DocumentCreatedResponse",
    "DocumentSummary",
    "HealthResponse",
    "DailyReportOut",
    "ReportIncidentOut",
    "CloudConnectionOut",
    "TestConnectionResult",
    "PollResult",
]
