"""SQLAlchemy repository for per-user accounts (implements the port in domain/users/ports.py).
Same convention as infrastructure/db/repositories/projects.py.

Does not translate IntegrityError (unique username) into a domain error itself — that happens one
layer up, in app/application/users/manage.py (ManageUsers), matching where ManageProjects does the
same translation for its own unique-name constraint.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.entities import User
from app.infrastructure.db.orm import UserRow
from app.infrastructure.db.repositories.mappers import user_to_domain


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_username(self, username: str) -> User | None:
        row = (
            await self._s.execute(select(UserRow).where(UserRow.username == username))
        ).scalar_one_or_none()
        return user_to_domain(row) if row else None

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        row = await self._s.get(UserRow, user_id)
        return user_to_domain(row) if row else None

    async def add(self, user: User) -> User:
        row = UserRow(
            username=user.username,
            email=user.email,
            password_hash=user.password_hash,
            auth_provider=user.auth_provider,
            external_id=user.external_id,
            group_id=user.group_id,
        )
        self._s.add(row)
        await self._s.flush()
        await self._s.refresh(row)
        return user_to_domain(row)

    async def get_by_external_id(self, auth_provider: str, external_id: str) -> User | None:
        stmt = select(UserRow).where(
            UserRow.auth_provider == auth_provider, UserRow.external_id == external_id
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return user_to_domain(row) if row else None

    async def list_all(self) -> list[User]:
        rows = (await self._s.execute(select(UserRow))).scalars().all()
        return [user_to_domain(row) for row in rows]

    async def set_group(self, user_id: uuid.UUID, group_id: uuid.UUID | None) -> User | None:
        row = await self._s.get(UserRow, user_id)
        if row is None:
            return None
        row.group_id = group_id
        await self._s.flush()
        # Reload so the joined `group` (and with it the role and projects) reflects the new
        # membership rather than the one the row was loaded with.
        await self._s.refresh(row)
        return user_to_domain(row)

    async def update_password_hash(self, user_id: uuid.UUID, password_hash: str) -> User | None:
        row = await self._s.get(UserRow, user_id)
        if row is None:
            return None
        row.password_hash = password_hash
        await self._s.flush()
        return user_to_domain(row)

    async def delete(self, user_id: uuid.UUID) -> None:
        row = await self._s.get(UserRow, user_id)
        if row is not None:
            await self._s.delete(row)
            await self._s.flush()
