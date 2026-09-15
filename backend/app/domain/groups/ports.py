"""Persistence port for groups. Same dependency-inversion convention as domain/users/ports.py."""

from __future__ import annotations

import uuid
from typing import Protocol

from app.domain.groups.entities import Group

__all__ = ["GroupRepository"]


class GroupRepository(Protocol):
    async def add(self, group: Group) -> Group: ...

    async def get(self, group_id: uuid.UUID) -> Group | None: ...

    async def list(self) -> list[Group]: ...

    async def update(self, group: Group) -> Group:
        """Replaces name/role/description and the project list wholesale — the admin screen edits
        the projects as a set, so a diff here could only drift from what was submitted."""
        ...

    async def delete(self, group_id: uuid.UUID) -> None:
        """Members fall back to Guest (no permissions, no projects) — the FK is ON DELETE SET NULL,
        so deleting a group revokes rather than orphans."""
        ...

    async def count_members_with_role(self, role: str) -> int:
        """How many accounts currently resolve to `role`. The last-admin guard reads this."""
        ...
