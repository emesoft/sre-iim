"""Domain entities for cloud connections (per-account AWS credentials) and alarm polling state.

Plain dataclasses, no ORM/framework coupling — same convention as domain/incidents/entities.py.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass
class CloudConnection:
    """One AWS account IIM watches for CloudWatch alarms."""

    project: str
    env: str
    region: str
    auth_type: str  # sso | access_key
    cloud: str = "aws"
    sso_profile_name: str | None = None
    encrypted_access_key_id: str | None = None
    encrypted_secret_access_key: str | None = None
    last_poll_at: datetime | None = None
    last_poll_status: str | None = None  # ok | error
    last_poll_error: str | None = None
    last_poll_alarm_count: int | None = None
    id: uuid.UUID | None = None
    created_at: datetime | None = None


@dataclass
class TrackedAlarm:
    """Last-seen state of one CloudWatch alarm for one connection, and the incident it opened
    (if any), so the poller can tell OK->ALARM from an alarm that's already open."""

    connection_id: uuid.UUID
    alarm_arn: str
    alarm_name: str
    last_state: str  # OK | ALARM
    incident_id: uuid.UUID | None = None
    id: uuid.UUID | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class AlarmState:
    """One alarm's current state, as read from CloudWatch `describe_alarms` for this poll cycle."""

    arn: str
    name: str
    state: str  # OK | ALARM | INSUFFICIENT_DATA
    reason: str | None = None
    metric_name: str | None = None
    namespace: str | None = None
