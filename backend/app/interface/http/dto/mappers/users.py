"""Mappers: User domain entity -> response DTO. Never carries `password_hash` past this layer."""

from __future__ import annotations

from app.domain.users.entities import User
from app.interface.http.dto.response.auth import UserOut


def user_out(user: User) -> UserOut:
    return UserOut(id=user.id, email=user.email, role=user.role, created_at=user.created_at)
