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
from app.domain.users.errors import (
    LastAdminError,
    NotALocalAccountError,
    UsernameTakenError,
    UserNotFoundError,
)
from app.domain.users.ports import UserRepository
from app.infrastructure.security.passwords import hash_password


@dataclass
class ManageUsers:
    users: UserRepository
    uow: UnitOfWork

    async def create(
        self,
        username: str,
        password: str,
        group_id: uuid.UUID | None = None,
        email: str | None = None,
    ) -> User:
        """A new account starts in whatever group the admin picked, or none — Guest, which can
        read nothing until someone assigns one."""
        try:
            user = await self.users.add(
                User(
                    username=username,
                    email=email,
                    password_hash=hash_password(password),
                    group_id=group_id,
                )
            )
        except IntegrityError as exc:
            await self.uow.rollback()
            raise UsernameTakenError(username) from exc
        await self.uow.commit()
        return user

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self.users.get_by_id(user_id)

    async def list_all(self) -> list[User]:
        return await self.users.list_all()

    async def reset_password(self, user_id: uuid.UUID, new_password: str) -> User:
        existing = await self.users.get_by_id(user_id)
        if existing is None:
            raise UserNotFoundError(user_id)
        if existing.auth_provider != "local":
            raise NotALocalAccountError(existing.username, existing.auth_provider)
        user = await self.users.update_password_hash(user_id, hash_password(new_password))
        if user is None:
            raise UserNotFoundError(user_id)
        await self.uow.commit()
        return user

    async def delete(self, user_id: uuid.UUID) -> None:
        existing = await self.users.get_by_id(user_id)
        if existing is None:
            raise UserNotFoundError(user_id)
        await self._refuse_if_last_admin(existing, "delete")
        await self.users.delete(user_id)
        await self.uow.commit()

    async def _refuse_if_last_admin(self, user: User, action: str) -> None:
        """Both guards live here rather than in the HTTP layer: "don't lock everyone out" is a
        rule about the system's state, not about one request, and the UI must not be the only
        thing enforcing it."""
        if user.role != "admin":
            return
        admins = [u for u in await self.users.list_all() if u.role == "admin"]
        if len(admins) <= 1:
            raise LastAdminError(action)
