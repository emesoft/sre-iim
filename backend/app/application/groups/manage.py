"""ManageGroups: admin CRUD over groups and which one an account belongs to.

The rules here are all about states nothing else can recover from. A group carries the permission
level, so the last-admin guard lives at this layer too: demoting the only admin group, deleting it,
or moving the only admin out of it all end in the same place — a system nobody can administer, with
no way back in through the app.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from app.domain.groups.entities import Group
from app.domain.groups.ports import GroupRepository
from app.domain.shared import UnitOfWork
from app.domain.users.errors import LastAdminError, UserNotFoundError
from app.domain.users.ports import UserRepository


class GroupNameTakenError(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"a group named '{name}' already exists")
        self.name = name


class GroupNotFoundError(Exception):
    def __init__(self, group_id: uuid.UUID) -> None:
        super().__init__(f"group {group_id} not found")
        self.group_id = group_id


@dataclass
class ManageGroups:
    groups: GroupRepository
    users: UserRepository
    uow: UnitOfWork

    async def list(self) -> list[Group]:
        return await self.groups.list()

    async def create(
        self,
        name: str,
        role: str,
        *,
        description: str | None = None,
        projects: tuple[str, ...] = (),
        model_profile_id: str | None = None,
    ) -> Group:
        try:
            group = await self.groups.add(
                Group(
                    name=name, role=role, description=description, projects=projects,
                    model_profile_id=model_profile_id,
                )
            )
        except IntegrityError as exc:
            await self.uow.rollback()
            raise GroupNameTakenError(name) from exc
        await self.uow.commit()
        return group

    async def update(
        self,
        group_id: uuid.UUID,
        *,
        name: str,
        role: str,
        description: str | None,
        projects: tuple[str, ...],
        model_profile_id: str | None,
    ) -> Group:
        existing = await self.groups.get(group_id)
        if existing is None:
            raise GroupNotFoundError(group_id)
        if existing.role == "admin" and role != "admin":
            await self._refuse_if_last_admins(group_id, "demote the group")
        try:
            group = await self.groups.update(
                Group(
                    id=group_id,
                    name=name,
                    role=role,
                    description=description,
                    projects=projects,
                    model_profile_id=model_profile_id,
                )
            )
        except IntegrityError as exc:
            await self.uow.rollback()
            raise GroupNameTakenError(name) from exc
        await self.uow.commit()
        return group

    async def delete(self, group_id: uuid.UUID) -> None:
        existing = await self.groups.get(group_id)
        if existing is None:
            raise GroupNotFoundError(group_id)
        if existing.role == "admin":
            await self._refuse_if_last_admins(group_id, "delete the group")
        await self.groups.delete(group_id)
        await self.uow.commit()

    async def assign(self, user_id: uuid.UUID, group_id: uuid.UUID | None):
        """Move one account into a group, or out of every group (Guest)."""
        user = await self.users.get_by_id(user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        if group_id is not None and await self.groups.get(group_id) is None:
            raise GroupNotFoundError(group_id)
        if user.role == "admin":
            target = await self.groups.get(group_id) if group_id else None
            if target is None or target.role != "admin":
                if await self.groups.count_members_with_role("admin") <= 1:
                    raise LastAdminError("move the last admin out of an admin group")
        updated = await self.users.set_group(user_id, group_id)
        await self.uow.commit()
        return updated

    async def _refuse_if_last_admins(self, group_id: uuid.UUID, action: str) -> None:
        """Refuses when this group's members are the only admins left.

        Counting members with the admin role (rather than admin groups) is what makes an empty
        second admin group not count as a safety net — it grants nobody anything.
        """
        total = await self.groups.count_members_with_role("admin")
        members = [u for u in await self.users.list_all() if u.group_id == group_id]
        if total and total - len([u for u in members if u.role == "admin"]) == 0:
            raise LastAdminError(action)
