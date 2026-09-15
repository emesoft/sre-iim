"""Mappers: User domain entity -> response DTO. Never carries `password_hash` past this layer."""

from __future__ import annotations

from app.domain.users.entities import User
from app.interface.http.dto.response.auth import UserOut


def user_out(user: User) -> UserOut:
    """`role` and `projects` come off the user's group — see User for why they aren't stored on
    the account itself."""
    return UserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        auth_provider=user.auth_provider,
        group_id=user.group_id,
        group_name=user.group_name,
        projects=list(user.projects),
        created_at=user.created_at,
    )
