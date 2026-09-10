"""User-management HTTP controller: admin CRUD over accounts. Admin-gated, same shape as
projects.py."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.application.users.manage import ManageUsers
from app.domain.users.entities import ROLES
from app.domain.users.errors import UsernameTakenError, UserNotFoundError
from app.interface.http.deps import get_manage_users, require_role
from app.interface.http.dto import mappers
from app.interface.http.dto.request import CreateUserRequest, UpdateUserRequest
from app.interface.http.dto.response import UserOut

router = APIRouter(
    prefix="/api/users", tags=["users"], dependencies=[Depends(require_role("admin"))]
)


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"role must be one of {list(ROLES)}",
        )


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateUserRequest,
    manager: ManageUsers = Depends(get_manage_users),
) -> UserOut:
    _check_role(body.role)
    try:
        user = await manager.create(body.username, body.password, body.role, email=body.email)
    except UsernameTakenError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return mappers.user_out(user)


@router.get("", response_model=list[UserOut])
async def list_users(manager: ManageUsers = Depends(get_manage_users)) -> list[UserOut]:
    users = await manager.list_all()
    return [mappers.user_out(u) for u in users]


@router.patch("/{user_id}", response_model=UserOut)
async def update_user_role(
    user_id: uuid.UUID,
    body: UpdateUserRequest,
    manager: ManageUsers = Depends(get_manage_users),
) -> UserOut:
    _check_role(body.role)
    try:
        user = await manager.update_role(user_id, body.role)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
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
