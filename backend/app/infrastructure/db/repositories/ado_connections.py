"""SQLAlchemy repository for per-project Azure DevOps connections (implements the port in
domain/ado_connections/ports.py). Same convention as infrastructure/db/repositories/cloud_connections.py.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.ado_connections.entities import AdoConnection
from app.infrastructure.db.orm import AdoConnectionRow
from app.infrastructure.db.repositories.mappers import ado_connection_to_domain


class SqlAlchemyAdoConnectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, connection: AdoConnection) -> AdoConnection:
        row = AdoConnectionRow(
            project=connection.project,
            org=connection.org,
            ado_project=connection.ado_project,
            encrypted_pat=connection.encrypted_pat,
            work_item_type=connection.work_item_type,
        )
        self._s.add(row)
        await self._s.flush()
        await self._s.refresh(row)
        return ado_connection_to_domain(row)

    async def get(self, connection_id: uuid.UUID) -> AdoConnection | None:
        row = await self._s.get(AdoConnectionRow, connection_id)
        return ado_connection_to_domain(row) if row else None

    async def get_by_project(self, project: str) -> AdoConnection | None:
        row = (
            await self._s.execute(
                select(AdoConnectionRow).where(AdoConnectionRow.project == project)
            )
        ).scalar_one_or_none()
        return ado_connection_to_domain(row) if row else None

    async def list(self) -> list[AdoConnection]:
        rows = (await self._s.execute(select(AdoConnectionRow))).scalars().all()
        return [ado_connection_to_domain(row) for row in rows]

    async def update(self, connection: AdoConnection) -> AdoConnection:
        row = await self._s.get(AdoConnectionRow, connection.id)
        if row is None:
            raise ValueError(f"connection {connection.id} not found")
        row.project = connection.project
        row.org = connection.org
        row.ado_project = connection.ado_project
        row.encrypted_pat = connection.encrypted_pat
        row.work_item_type = connection.work_item_type
        await self._s.flush()
        return ado_connection_to_domain(row)

    async def delete(self, connection_id: uuid.UUID) -> None:
        row = await self._s.get(AdoConnectionRow, connection_id)
        if row is not None:
            await self._s.delete(row)
            await self._s.flush()
