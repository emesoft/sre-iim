"""Domain-level errors for the project registry. The application layer raises these after
translating a database constraint violation (unique name, or a connection's foreign key) — this
module itself stays framework-agnostic, no SQLAlchemy/asyncpg imports.
"""

from __future__ import annotations

import uuid


class ProjectNameTakenError(Exception):
    """Raised creating a project whose name already exists in the registry."""

    def __init__(self, name: str) -> None:
        super().__init__(f"a project named '{name}' already exists")
        self.name = name


class ProjectInUseError(Exception):
    """Raised deleting a project that's still referenced by at least one connection."""

    def __init__(self, project_id: uuid.UUID) -> None:
        super().__init__(f"project {project_id} is still referenced by one or more connections")
        self.project_id = project_id


class UnknownProjectError(Exception):
    """Raised creating or updating a connection whose `project` isn't in the registry."""

    def __init__(self, name: str) -> None:
        super().__init__(f"unknown project '{name}' — add it in the Projects registry first")
        self.name = name
