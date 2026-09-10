"""ManageUsers: admin CRUD over user accounts. Translates the database-level unique-username
constraint violation into a domain error here — same convention as ManageProjects translating its
own unique-name constraint.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from app.domain.shared import UnitOfWork
from app.domain.users.entities import User
from app.domain.users.errors import UsernameTakenError, UserNotFoundError
from app.domain.users.ports import UserRepository
from app.infrastructure.security.passwords import hash_password


@dataclass
class ManageUsers:
    users: UserRepository
    uow: UnitOfWork

    async def create(self, username: str, password: str, role: str, email: str | None = None) -> User:
        try:
            user = await self.users.add(
                User(
                    username=username,
                    email=email,
                    password_hash=hash_password(password),
                    role=role,
                )
            )
        except IntegrityError as exc:
            await self.uow.rollback()
            raise UsernameTakenError(username) from exc
        await self.uow.commit()
        return user

    async def list_all(self) -> list[User]:
        return await self.users.list_all()

    async def update_role(self, user_id: uuid.UUID, role: str) -> User:
        user = await self.users.update_role(user_id, role)
        if user is None:
            raise UserNotFoundError(user_id)
        await self.uow.commit()
        return user

    async def delete(self, user_id: uuid.UUID) -> None:
        existing = await self.users.get_by_id(user_id)
        if existing is None:
            raise UserNotFoundError(user_id)
        await self.users.delete(user_id)
        await self.uow.commit()
