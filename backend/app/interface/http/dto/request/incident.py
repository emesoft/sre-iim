"""Incident request DTOs (the parse-first boundary)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class IncidentIngestRequest(BaseModel):
    """`POST /api/incidents` body: a source label plus the raw incident context dict."""

    source: str = "manual"  # auto | manual | webhook
    context: dict = Field(..., description="Incident context; must contain 'service'.")


class ChatMessageRequest(BaseModel):
    """`POST /api/incidents/{id}/chat` body."""

    message: str = Field(..., min_length=1)


class ResolveIncidentRequest(BaseModel):
    """`POST /api/incidents/{id}/resolve` body: how the incident was actually fixed."""

    resolution_notes: str = Field(..., min_length=1)
