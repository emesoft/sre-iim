"""Incident request DTOs (the parse-first boundary)."""

from __future__ import annotations

import uuid

from typing import Literal

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


class BulkIncidentRequest(BaseModel):
    """`POST /api/incidents/bulk` — the same action applied to several incidents.

    Capped rather than unbounded: "resolve everything" is a plausible mis-click, and for `analyze`
    every id is a paid LLM call. A refused request the caller can retry in pages is better than one
    that quietly spends a lot of money.
    """

    action: Literal["resolve", "analyze"]
    incident_ids: list[uuid.UUID] = Field(min_length=1, max_length=25)
    #: Only meaningful for `resolve`.
    resolution_notes: str | None = None
