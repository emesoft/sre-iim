"""Ports the project-registry use cases depend on. Implemented in the infrastructure layer.

Same dependency-inversion convention as domain/ado_connections/ports.py.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from app.domain.projects.entities import Project

__all__ = ["ProjectRepository"]


class ProjectRepository(Protocol):
    """Persistence for the shared project registry."""

    async def add(self, project: Project) -> Project: ...

    async def get(self, project_id: uuid.UUID) -> Project | None: ...

    async def list(self) -> list[Project]: ...

    async def delete(self, project_id: uuid.UUID) -> None: ...
