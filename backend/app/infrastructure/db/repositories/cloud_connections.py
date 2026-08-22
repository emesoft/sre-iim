"""SQLAlchemy repositories for cloud connections and tracked alarms (implements the ports in
domain/cloud_connections/ports.py). Same convention as infrastructure/db/repositories/incidents.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cloud_connections.entities import CloudConnection, TrackedAlarm
from app.infrastructure.db.orm import CloudConnectionRow, TrackedAlarmRow
from app.infrastructure.db.repositories.mappers import (
    cloud_connection_to_domain,
    tracked_alarm_to_domain,
)


class SqlAlchemyCloudConnectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, connection: CloudConnection) -> CloudConnection:
        row = CloudConnectionRow(
            project=connection.project,
            env=connection.env,
            cloud=connection.cloud,
            region=connection.region,
            auth_type=connection.auth_type,
            sso_profile_name=connection.sso_profile_name,
            encrypted_access_key_id=connection.encrypted_access_key_id,
            encrypted_secret_access_key=connection.encrypted_secret_access_key,
        )
        self._s.add(row)
        await self._s.flush()
        await self._s.refresh(row)
        return cloud_connection_to_domain(row)

    async def get(self, connection_id: uuid.UUID) -> CloudConnection | None:
        row = await self._s.get(CloudConnectionRow, connection_id)
        return cloud_connection_to_domain(row) if row else None

    async def list(self) -> list[CloudConnection]:
        rows = (await self._s.execute(select(CloudConnectionRow))).scalars().all()
        return [cloud_connection_to_domain(row) for row in rows]

    async def delete(self, connection_id: uuid.UUID) -> None:
        row = await self._s.get(CloudConnectionRow, connection_id)
        if row is not None:
            await self._s.delete(row)
            await self._s.flush()

    async def update(self, connection: CloudConnection) -> CloudConnection:
        row = await self._s.get(CloudConnectionRow, connection.id)
        if row is None:
            raise ValueError(f"connection {connection.id} not found")
        row.project = connection.project
        row.env = connection.env
        row.region = connection.region
        row.auth_type = connection.auth_type
        row.sso_profile_name = connection.sso_profile_name
        row.encrypted_access_key_id = connection.encrypted_access_key_id
        row.encrypted_secret_access_key = connection.encrypted_secret_access_key
        await self._s.flush()
        return cloud_connection_to_domain(row)

    async def record_poll_result(
        self, connection_id: uuid.UUID, *, status: str, error: str | None, alarm_count: int | None = None
    ) -> None:
        row = await self._s.get(CloudConnectionRow, connection_id)
        if row is None:
            return
        row.last_poll_at = datetime.now(timezone.utc)
        row.last_poll_status = status
        row.last_poll_error = error
        row.last_poll_alarm_count = alarm_count
        await self._s.flush()


class SqlAlchemyTrackedAlarmRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, connection_id: uuid.UUID, alarm_arn: str) -> TrackedAlarm | None:
        stmt = select(TrackedAlarmRow).where(
            TrackedAlarmRow.connection_id == connection_id, TrackedAlarmRow.alarm_arn == alarm_arn
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return tracked_alarm_to_domain(row) if row else None

    async def upsert(
        self,
        connection_id: uuid.UUID,
        *,
        alarm_arn: str,
        alarm_name: str,
        last_state: str,
        incident_id: uuid.UUID | None,
    ) -> None:
        stmt = select(TrackedAlarmRow).where(
            TrackedAlarmRow.connection_id == connection_id, TrackedAlarmRow.alarm_arn == alarm_arn
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = TrackedAlarmRow(connection_id=connection_id, alarm_arn=alarm_arn)
            self._s.add(row)
        row.alarm_name = alarm_name
        row.last_state = last_state
        row.incident_id = incident_id
        await self._s.flush()
