"""Cloud-connection response DTOs. Deliberately omits the encrypted secret fields entirely — the
API never round-trips them, not even masked (design spec "Credentials & security")."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class CloudConnectionOut(BaseModel):
    """One row in `GET /api/cloud-connections`."""

    id: uuid.UUID
    project: str
    env: str
    cloud: str
    region: str
    auth_type: str
    sso_profile_name: str | None
    has_access_key: bool
    last_poll_at: datetime | None
    last_poll_status: str | None
    last_poll_error: str | None
    last_poll_alarm_count: int | None
    created_at: datetime


class TestConnectionResult(BaseModel):
    """`POST /api/cloud-connections/{id}/test` response."""

    ok: bool
    error: str | None


class PollResult(BaseModel):
    """`POST /api/cloud-connections/poll` (and `/{id}/poll`) response — lets the UI show
    "refresh worked, N alarms active" (or "M connections failed") right after the click, without
    a second round-trip to re-fetch connections."""

    polled: int
    alarm_count: int
    errors: int


class PollScheduleOut(BaseModel):
    """`GET /api/cloud-connections/poll-schedule` response — the background poll is one global
    APScheduler job covering every connection, not a per-connection timer, so this reflects the
    scheduler's own next-run time rather than being derived from any one connection's last poll."""

    interval_minutes: int
    next_run_at: datetime | None
