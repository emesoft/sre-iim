"""User-management HTTP controller: admin CRUD over accounts. Admin-gated, same shape as
projects.py."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.application.groups.manage import GroupNotFoundError, ManageGroups
from app.application.users.manage import ManageUsers
from app.domain.users.errors import (
    LastAdminError,
    NotALocalAccountError,
    UsernameTakenError,
    UserNotFoundError,
)
from app.interface.http.deps import get_manage_groups, get_manage_users, require_role
from app.interface.http.dto import mappers
from app.interface.http.dto.request import (
    AssignGroupRequest,
    CreateUserRequest,
    ResetPasswordRequest,
)
from app.interface.http.dto.response import UserOut

router = APIRouter(
    prefix="/api/users", tags=["users"], dependencies=[Depends(require_role("admin"))]
)


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateUserRequest,
    manager: ManageUsers = Depends(get_manage_users),
) -> UserOut:
    try:
        user = await manager.create(
            body.username, body.password, body.group_id, email=body.email
        )
    except UsernameTakenError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return mappers.user_out(user)


@router.get("", response_model=list[UserOut])
async def list_users(manager: ManageUsers = Depends(get_manage_users)) -> list[UserOut]:
    """Each account with its group, and the role and projects that group resolves to."""
    return [mappers.user_out(u) for u in await manager.list_all()]


@router.put("/{user_id}/group", response_model=UserOut)
async def assign_group(
    user_id: uuid.UUID,
    body: AssignGroupRequest,
    groups: ManageGroups = Depends(get_manage_groups),
) -> UserOut:
    """The one control that changes what an account may do and see — they're the same decision."""
    try:
        user = await groups.assign(user_id, body.group_id)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except GroupNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LastAdminError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return mappers.user_out(user)


@router.post("/{user_id}/password", response_model=UserOut)
async def reset_password(
    user_id: uuid.UUID,
    body: ResetPasswordRequest,
    manager: ManageUsers = Depends(get_manage_users),
) -> UserOut:
    try:
        user = await manager.reset_password(user_id, body.new_password)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except NotALocalAccountError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return mappers.user_out(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    manager: ManageUsers = Depends(get_manage_users),
) -> None:
    try:
        await manager.delete(user_id)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LastAdminError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
