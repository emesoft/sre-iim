"""Ports the cloud-connection use cases depend on. Implemented in the infrastructure layer.

Same dependency-inversion convention as domain/incidents/ports.py.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from app.domain.cloud_connections.entities import AlarmState, CloudConnection, TrackedAlarm

__all__ = ["CloudConnectionRepository", "TrackedAlarmRepository", "AlarmFetcher"]


class CloudConnectionRepository(Protocol):
    """Persistence for cloud connections."""

    async def add(self, connection: CloudConnection) -> CloudConnection: ...

    async def get(self, connection_id: uuid.UUID) -> CloudConnection | None: ...

    async def list(self) -> list[CloudConnection]: ...

    async def delete(self, connection_id: uuid.UUID) -> None: ...

    async def update(self, connection: CloudConnection) -> CloudConnection:
        """Persist a full replacement of an existing connection's editable fields (project, env,
        region, auth_type, sso_profile_name, encrypted credentials). `connection.id` selects the
        row; poll-tracking fields (last_poll_*) are untouched."""
        ...

    async def record_poll_result(
        self, connection_id: uuid.UUID, *, status: str, error: str | None, alarm_count: int | None = None
    ) -> None:
        """Update last_poll_at (now)/last_poll_status/last_poll_error/last_poll_alarm_count after a
        poll attempt. `alarm_count` is the number of alarms seen in ALARM state this poll — None on
        an error (the fetch never got far enough to count anything)."""
        ...


class TrackedAlarmRepository(Protocol):
    """Persistence for per-alarm polling state."""

    async def get(self, connection_id: uuid.UUID, alarm_arn: str) -> TrackedAlarm | None: ...

    async def upsert(
        self,
        connection_id: uuid.UUID,
        *,
        alarm_arn: str,
        alarm_name: str,
        last_state: str,
        incident_id: uuid.UUID | None,
    ) -> None: ...


class AlarmFetcher(Protocol):
    """Reads the current alarm states for one connection's AWS account."""

    async def list_alarms(self, connection: CloudConnection) -> list[AlarmState]: ...
