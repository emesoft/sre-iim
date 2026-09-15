"""SQLAlchemy repository for groups (implements the port in domain/groups/ports.py)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.groups.entities import Group
from app.infrastructure.db.orm import GroupProjectRow, GroupRow, UserRow
from app.infrastructure.db.repositories.mappers import group_to_domain


class SqlAlchemyGroupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def _with_counts(self, groups: list[Group]) -> list[Group]:
        """Member counts in one query rather than per group — the admin screen shows them all."""
        counts = dict(
            (
                await self._s.execute(
                    select(UserRow.group_id, func.count())
                    .where(UserRow.group_id.is_not(None))
                    .group_by(UserRow.group_id)
                )
            ).all()
        )
        for g in groups:
            g.member_count = counts.get(g.id, 0)
        return groups

    async def add(self, group: Group) -> Group:
        row = GroupRow(
            name=group.name, role=group.role, description=group.description,
            model_profile_id=group.model_profile_id,
        )
        row.projects = [GroupProjectRow(project=p) for p in sorted(set(group.projects))]
        self._s.add(row)
        await self._s.flush()
        return group_to_domain(row)

    async def get(self, group_id: uuid.UUID) -> Group | None:
        row = await self._s.get(GroupRow, group_id)
        return group_to_domain(row) if row else None

    async def list(self) -> list[Group]:
        rows = (await self._s.execute(select(GroupRow).order_by(GroupRow.name))).scalars().all()
        return await self._with_counts([group_to_domain(r) for r in rows])

    async def update(self, group: Group) -> Group:
        row = await self._s.get(GroupRow, group.id)
        if row is None:
            raise ValueError(f"group {group.id} not found")
        row.name = group.name
        row.role = group.role
        row.description = group.description
        row.model_profile_id = group.model_profile_id
        # Replaced wholesale: the form submits the complete set, so reconciling a diff here could
        # only ever disagree with what the admin saw.
        row.projects = [GroupProjectRow(project=p) for p in sorted(set(group.projects))]
        await self._s.flush()
        return group_to_domain(row)

    async def delete(self, group_id: uuid.UUID) -> None:
        row = await self._s.get(GroupRow, group_id)
        if row is not None:
            await self._s.delete(row)
            await self._s.flush()

    async def count_members_with_role(self, role: str) -> int:
        return (
            await self._s.execute(
                select(func.count())
                .select_from(UserRow)
                .join(GroupRow, GroupRow.id == UserRow.group_id)
                .where(GroupRow.role == role)
            )
        ).scalar_one()
