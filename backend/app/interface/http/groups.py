"""Group HTTP controller: admin CRUD over groups — what members may do and which projects they see."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.application.groups.manage import GroupNameTakenError, GroupNotFoundError, ManageGroups
from app.domain.groups.entities import GROUP_ROLES, Group
from app.domain.users.errors import LastAdminError
from app.interface.http.deps import get_manage_groups, require_role
from app.interface.http.dto.request import GroupWriteRequest
from app.interface.http.dto.response import GroupOut

router = APIRouter(
    prefix="/api/groups", tags=["groups"], dependencies=[Depends(require_role("admin"))]
)


def _out(group: Group) -> GroupOut:
    return GroupOut(
        id=group.id,
        name=group.name,
        role=group.role,
        description=group.description,
        projects=list(group.projects),
        model_profile_id=group.model_profile_id,
        member_count=group.member_count,
        created_at=group.created_at,
    )


def _check(body: GroupWriteRequest) -> tuple[str, ...]:
    if body.role not in GROUP_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"role must be one of {list(GROUP_ROLES)}",
        )
    # An admin group is unrestricted by role, so project names on it would describe a limit the
    # data layer doesn't apply — dropping them keeps the screen honest.
    return () if body.role == "admin" else tuple(body.projects)


@router.get("", response_model=list[GroupOut])
async def list_groups(manager: ManageGroups = Depends(get_manage_groups)) -> list[GroupOut]:
    return [_out(g) for g in await manager.list()]


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
async def create_group(
    body: GroupWriteRequest, manager: ManageGroups = Depends(get_manage_groups)
) -> GroupOut:
    try:
        group = await manager.create(
            body.name.strip(),
            body.role,
            description=body.description,
            projects=_check(body),
            model_profile_id=body.model_profile_id or None,
        )
    except GroupNameTakenError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _out(group)


@router.patch("/{group_id}", response_model=GroupOut)
async def update_group(
    group_id: uuid.UUID,
    body: GroupWriteRequest,
    manager: ManageGroups = Depends(get_manage_groups),
) -> GroupOut:
    try:
        group = await manager.update(
            group_id,
            name=body.name.strip(),
            role=body.role,
            description=body.description,
            projects=_check(body),
            model_profile_id=body.model_profile_id or None,
        )
    except GroupNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except GroupNameTakenError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except LastAdminError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _out(group)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: uuid.UUID, manager: ManageGroups = Depends(get_manage_groups)
) -> None:
    """Members fall back to Guest — they lose the permissions and the projects together."""
    try:
        await manager.delete(group_id)
    except GroupNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LastAdminError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
