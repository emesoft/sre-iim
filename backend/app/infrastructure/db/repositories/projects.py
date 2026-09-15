"""SQLAlchemy repository for the shared project registry (implements the port in
domain/projects/ports.py). Same convention as infrastructure/db/repositories/ado_connections.py.

Does not translate IntegrityError into a domain error itself — that happens one layer up, in
app/application/projects/manage.py (ManageProjects), matching where ManageCloudConnections and
ManageAdoConnections do the same translation for the new FK violation.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.projects.entities import Project
from app.infrastructure.db.orm import ProjectRow
from app.infrastructure.db.repositories.mappers import project_to_domain


class SqlAlchemyProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, project: Project) -> Project:
        row = ProjectRow(name=project.name)
        self._s.add(row)
        await self._s.flush()
        await self._s.refresh(row)
        return project_to_domain(row)

    async def get(self, project_id: uuid.UUID) -> Project | None:
        row = await self._s.get(ProjectRow, project_id)
        return project_to_domain(row) if row else None

    async def list(self) -> list[Project]:
        rows = (await self._s.execute(select(ProjectRow))).scalars().all()
        return [project_to_domain(row) for row in rows]

    async def set_auto_analyze(self, project_id: uuid.UUID, enabled: bool) -> Project:
        row = await self._s.get(ProjectRow, project_id)
        if row is None:
            raise ValueError(f"project {project_id} not found")
        row.auto_analyze = enabled
        await self._s.flush()
        return project_to_domain(row)

    async def delete(self, project_id: uuid.UUID) -> None:
        row = await self._s.get(ProjectRow, project_id)
        if row is not None:
            await self._s.delete(row)
            await self._s.flush()
