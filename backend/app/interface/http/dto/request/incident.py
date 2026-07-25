"""Incident request DTOs (the parse-first boundary)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class IncidentIngestRequest(BaseModel):
    """`POST /api/incidents` body: a source label plus the raw incident context dict."""

    source: str = "manual"  # auto | manual | webhook
    context: dict = Field(..., description="Incident context; must contain 'service'.")


class LogSearchRequest(BaseModel):
    """`POST /api/incidents/{id}/logs/search` body: the log group + time window to query."""

    log_group: str
    start: datetime
    end: datetime
    filter_pattern: str | None = Field(
        default=None, description="Insights `like` regex; defaults to a generic error pattern."
    )


class ResolveIncidentRequest(BaseModel):
    """`POST /api/incidents/{id}/resolve` body: how the incident was actually fixed."""

    resolution_notes: str = Field(..., min_length=1)
