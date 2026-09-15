"""Integration response DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CapabilityHealthOut(BaseModel):
    """Last run of one capability. Separate per capability because they fail independently."""

    capability: str
    last_run_at: datetime | None = None
    status: str | None = None  # ok | error
    error: str | None = None
    item_count: int | None = None


class IntegrationOut(BaseModel):
    """One row in `GET /api/integrations`.

    `config` is echoed back (it holds no secrets); `secret_names` lists which credentials are
    stored **without** their values, so the form can show "already set" without ever sending a
    credential back to the browser.
    """

    id: uuid.UUID
    project: str
    env: str
    provider: str
    display_name: str | None = None
    enabled: bool
    config: dict = Field(default_factory=dict)
    secret_names: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    health: list[CapabilityHealthOut] = Field(default_factory=list)
    created_at: datetime


class ProviderOut(BaseModel):
    """One entry of `GET /api/providers` — the registry's declaration, which the Settings form
    builds itself from rather than hard-coding a field list per provider."""

    provider: str
    label: str
    capabilities: list[str]
    required_config: list[str]
    secret_names: list[str]
    notes: str = ""


class TestConnectionResult(BaseModel):
    """`POST /api/integrations/{id}/test` — whether a live call with the stored credentials
    worked, and the provider's own message when it didn't."""

    ok: bool
    error: str | None = None


class PollResult(BaseModel):
    """`POST /api/integrations/poll` (all) or `/{id}/poll` (one)."""

    polled: int
    alarm_count: int
    errors: int


class PollScheduleOut(BaseModel):
    """`GET /api/integrations/poll-schedule` — the background sweep's cadence and next fire time,
    so the Settings page can say when it will next run rather than implying it's continuous."""

    interval_minutes: int
    next_run_at: datetime | None = None


class SsoBeginOut(BaseModel):
    """What the operator needs to approve the sign-in, and the handle to poll with. The client
    secret AWS issued stays on the server — it never travels to the browser."""

    handle: str
    verification_uri_complete: str
    user_code: str
    interval_seconds: int
    expires_in_seconds: int


class SsoRoleOut(BaseModel):
    account_id: str
    account_name: str
    role_name: str


class SsoPollOut(BaseModel):
    """`pending` until the operator approves; then the account/role pairs they can reach."""

    status: str  # pending | ready | expired
    roles: list[SsoRoleOut] = Field(default_factory=list)
