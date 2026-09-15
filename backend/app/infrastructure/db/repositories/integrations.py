"""SQLAlchemy repository for integrations and their per-capability health (implements the port in
domain/integrations/ports.py).

This is the direct, provider-agnostic view of the `integrations` table. The `CloudConnection` and
`AdoConnection` repositories still translate the same rows into the older per-provider shapes for
the HTTP API the Settings UI speaks — that translation goes away when the UI is rebuilt.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.integrations.entities import Integration, IntegrationHealth, TrackedAlarm
from app.infrastructure.db.orm import IntegrationHealthRow, IntegrationRow, TrackedAlarmRow


def _to_domain(row: IntegrationRow) -> Integration:
    return Integration(
        id=row.id,
        project=row.project,
        env=row.env,
        provider=row.provider,
        config=dict(row.config or {}),
        encrypted_secrets=dict(row.encrypted_secrets or {}),
        capabilities=tuple(row.capabilities or ()),
        enabled=row.enabled,
        display_name=row.display_name,
        created_at=row.created_at,
    )


def _health_to_domain(row: IntegrationHealthRow) -> IntegrationHealth:
    return IntegrationHealth(
        integration_id=row.integration_id,
        capability=row.capability,
        last_run_at=row.last_run_at,
        status=row.status,
        error=row.error,
        item_count=row.item_count,
    )


class SqlAlchemyIntegrationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, integration_id: uuid.UUID) -> Integration | None:
        row = await self._s.get(IntegrationRow, integration_id)
        return _to_domain(row) if row else None

    async def list(
        self, *, capability: str | None = None, enabled_only: bool = False
    ) -> list[Integration]:
        stmt = select(IntegrationRow).order_by(IntegrationRow.project, IntegrationRow.created_at)
        if capability is not None:
            # Postgres array containment: the row's capabilities must include this one.
            stmt = stmt.where(IntegrationRow.capabilities.contains([capability]))
        if enabled_only:
            stmt = stmt.where(IntegrationRow.enabled.is_(True))
        rows = (await self._s.execute(stmt)).scalars().all()
        return [_to_domain(row) for row in rows]

    async def for_project(self, project: str, capability: str) -> Integration | None:
        stmt = (
            select(IntegrationRow)
            .where(
                IntegrationRow.project == project,
                IntegrationRow.capabilities.contains([capability]),
            )
            .order_by(IntegrationRow.created_at)
        )
        row = (await self._s.execute(stmt)).scalars().first()
        return _to_domain(row) if row else None

    async def for_provider(self, project: str, provider: str) -> Integration | None:
        stmt = (
            select(IntegrationRow)
            .where(IntegrationRow.project == project, IntegrationRow.provider == provider)
            .order_by(IntegrationRow.created_at)
        )
        row = (await self._s.execute(stmt)).scalars().first()
        return _to_domain(row) if row else None

    async def add(self, integration: Integration) -> Integration:
        row = IntegrationRow(
            project=integration.project,
            env=integration.env,
            provider=integration.provider,
            display_name=integration.display_name,
            enabled=integration.enabled,
            config=integration.config,
            encrypted_secrets=integration.encrypted_secrets,
            capabilities=list(integration.capabilities),
        )
        self._s.add(row)
        await self._s.flush()
        await self._s.refresh(row)
        return _to_domain(row)

    async def update(self, integration: Integration) -> Integration:
        row = await self._s.get(IntegrationRow, integration.id)
        if row is None:
            raise ValueError(f"integration {integration.id} not found")
        row.project = integration.project
        row.env = integration.env
        row.provider = integration.provider
        row.display_name = integration.display_name
        row.config = integration.config
        row.encrypted_secrets = integration.encrypted_secrets
        row.capabilities = list(integration.capabilities)
        await self._s.flush()
        return _to_domain(row)

    async def delete(self, integration_id: uuid.UUID) -> None:
        row = await self._s.get(IntegrationRow, integration_id)
        if row is not None:
            await self._s.delete(row)
            await self._s.flush()

    async def set_enabled(self, integration_id: uuid.UUID, enabled: bool) -> Integration:
        row = await self._s.get(IntegrationRow, integration_id)
        if row is None:
            raise ValueError(f"integration {integration_id} not found")
        row.enabled = enabled
        await self._s.flush()
        return _to_domain(row)

    async def health_for(self, integration_id: uuid.UUID) -> list[IntegrationHealth]:
        stmt = select(IntegrationHealthRow).where(
            IntegrationHealthRow.integration_id == integration_id
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [_health_to_domain(row) for row in rows]

    async def health(
        self, integration_id: uuid.UUID, capability: str
    ) -> IntegrationHealth | None:
        row = await self._s.get(IntegrationHealthRow, (integration_id, capability))
        return _health_to_domain(row) if row else None

    async def record_run(
        self,
        integration_id: uuid.UUID,
        capability: str,
        *,
        status: str,
        error: str | None,
        item_count: int | None = None,
    ) -> None:
        values = {
            "integration_id": integration_id,
            "capability": capability,
            "last_run_at": datetime.now(timezone.utc),
            "status": status,
            "error": error,
            "item_count": item_count,
        }
        stmt = pg_insert(IntegrationHealthRow).values(**values)
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    IntegrationHealthRow.integration_id,
                    IntegrationHealthRow.capability,
                ],
                set_={k: v for k, v in values.items() if k not in ("integration_id", "capability")},
            )
        )
        await self._s.flush()


class SqlAlchemyTrackedAlarmRepository:
    """Per-alarm polling state. `connection_id` is an `integrations.id` (migration 0019 re-pointed
    the FK while preserving ids, so existing rows kept working)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, connection_id: uuid.UUID, alarm_arn: str) -> TrackedAlarm | None:
        stmt = select(TrackedAlarmRow).where(
            TrackedAlarmRow.connection_id == connection_id, TrackedAlarmRow.alarm_arn == alarm_arn
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return _tracked_to_domain(row) if row else None

    async def upsert(
        self,
        connection_id: uuid.UUID,
        *,
        alarm_arn: str,
        alarm_name: str,
        last_state: str,
        incident_id: uuid.UUID | None,
        last_incident_id: uuid.UUID | None = None,
        occurrence_count: int | None = None,
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
        if last_incident_id is not None:
            row.last_incident_id = last_incident_id
        if occurrence_count is not None:
            row.occurrence_count = occurrence_count
        await self._s.flush()


def _tracked_to_domain(row: TrackedAlarmRow) -> TrackedAlarm:
    return TrackedAlarm(
        id=row.id,
        connection_id=row.connection_id,
        alarm_arn=row.alarm_arn,
        alarm_name=row.alarm_name,
        last_state=row.last_state,
        incident_id=row.incident_id,
        last_incident_id=row.last_incident_id,
        occurrence_count=row.occurrence_count,
        updated_at=row.updated_at,
    )
