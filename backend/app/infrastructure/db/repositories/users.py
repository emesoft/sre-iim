"""SQLAlchemy repository for per-user accounts (implements the port in domain/users/ports.py).
Same convention as infrastructure/db/repositories/projects.py.

Does not translate IntegrityError (unique email) into a domain error itself — that happens one
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

    async def get_by_email(self, email: str) -> User | None:
        row = (
            await self._s.execute(select(UserRow).where(UserRow.email == email))
        ).scalar_one_or_none()
        return user_to_domain(row) if row else None

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        row = await self._s.get(UserRow, user_id)
        return user_to_domain(row) if row else None

    async def add(self, user: User) -> User:
        row = UserRow(email=user.email, password_hash=user.password_hash, role=user.role)
        self._s.add(row)
        await self._s.flush()
        await self._s.refresh(row)
        return user_to_domain(row)

    async def list_all(self) -> list[User]:
        rows = (await self._s.execute(select(UserRow))).scalars().all()
        return [user_to_domain(row) for row in rows]

    async def update_role(self, user_id: uuid.UUID, role: str) -> User | None:
        row = await self._s.get(UserRow, user_id)
        if row is None:
            return None
        row.role = role
        await self._s.flush()
        return user_to_domain(row)

    async def delete(self, user_id: uuid.UUID) -> None:
        row = await self._s.get(UserRow, user_id)
        if row is not None:
            await self._s.delete(row)
            await self._s.flush()
