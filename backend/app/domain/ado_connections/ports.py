"""Ports the ADO-connection use cases depend on. Implemented in the infrastructure layer.

Same dependency-inversion convention as domain/cloud_connections/ports.py.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from app.domain.ado_connections.entities import AdoConnection

__all__ = ["AdoConnectionRepository"]


class AdoConnectionRepository(Protocol):
    """Persistence for per-project Azure DevOps connections."""

    async def add(self, connection: AdoConnection) -> AdoConnection: ...

    async def get(self, connection_id: uuid.UUID) -> AdoConnection | None: ...

    async def get_by_project(self, project: str) -> AdoConnection | None:
        """The connection to use when filing a ticket for an incident whose `service` is
        `project` — `None` means no ADO project is configured for it yet."""
        ...

    async def list(self) -> list[AdoConnection]: ...

    async def update(self, connection: AdoConnection) -> AdoConnection:
        """Persist a full replacement of an existing connection's editable fields.
        `connection.id` selects the row."""
        ...

    async def delete(self, connection_id: uuid.UUID) -> None: ...
