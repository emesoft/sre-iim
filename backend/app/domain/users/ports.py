"""Ports the user/auth use cases depend on. Implemented in the infrastructure layer.

Same dependency-inversion convention as domain/projects/ports.py.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from app.domain.users.entities import User

__all__ = ["UserRepository"]


class UserRepository(Protocol):
    """Persistence for per-user accounts."""

    async def get_by_username(self, username: str) -> User | None: ...

    async def get_by_id(self, user_id: uuid.UUID) -> User | None: ...

    async def get_by_external_id(self, auth_provider: str, external_id: str) -> User | None:
        """Look an account up by the identity provider's own stable id (Entra `oid`) rather than
        by username, which the provider allows the user to change."""
        ...

    async def add(self, user: User) -> User: ...

    async def list_all(self) -> list[User]: ...

    async def set_group(self, user_id: uuid.UUID, group_id: uuid.UUID | None) -> User | None:
        """Move an account into a group, or out of all of them (Guest). This is the only way a
        role changes — there is no role of its own to set."""
        ...

    async def update_password_hash(self, user_id: uuid.UUID, password_hash: str) -> User | None: ...

    async def delete(self, user_id: uuid.UUID) -> None: ...
