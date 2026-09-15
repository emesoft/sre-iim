"""ManageProjects: CRUD for the shared project registry that CloudConnection/AdoConnection (and
future per-project connection types) reference. Translates the database-level constraint
violations (unique name on create, FK-from-a-connection on delete) into domain errors here — same
convention as ManageCloudConnections/ManageAdoConnections translating the new FK violation on
their own `create`/`update` (see `.claude/specs/2026-08-23-project-registry-design.md`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from app.domain.projects.entities import Project
from app.domain.projects.errors import ProjectInUseError, ProjectNameTakenError
from app.domain.projects.ports import ProjectRepository
from app.domain.shared import UnitOfWork


@dataclass
class ManageProjects:
    projects: ProjectRepository
    uow: UnitOfWork

    async def create(self, name: str) -> Project:
        try:
            project = await self.projects.add(Project(name=name))
        except IntegrityError as exc:
            await self.uow.rollback()
            raise ProjectNameTakenError(name) from exc
        await self.uow.commit()
        return project

    async def list(self) -> list[Project]:
        return await self.projects.list()

    async def set_auto_analyze(self, project_id: uuid.UUID, enabled: bool) -> Project:
        """Pause/resume automatic triage for one project without touching the global setting."""
        project = await self.projects.set_auto_analyze(project_id, enabled)
        await self.uow.commit()
        return project

    async def delete(self, project_id: uuid.UUID) -> None:
        existing = await self.projects.get(project_id)
        if existing is None:
            raise ValueError(f"project {project_id} not found")
        try:
            await self.projects.delete(project_id)
        except IntegrityError as exc:
            await self.uow.rollback()
            raise ProjectInUseError(project_id) from exc
        await self.uow.commit()
