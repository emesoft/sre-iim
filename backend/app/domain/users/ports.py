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

    async def get_by_email(self, email: str) -> User | None: ...

    async def get_by_id(self, user_id: uuid.UUID) -> User | None: ...

    async def add(self, user: User) -> User: ...

    async def list_all(self) -> list[User]: ...

    async def update_role(self, user_id: uuid.UUID, role: str) -> User | None: ...

    async def delete(self, user_id: uuid.UUID) -> None: ...
