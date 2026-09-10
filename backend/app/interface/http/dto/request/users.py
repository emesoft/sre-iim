"""User-management request DTOs (the parse-first boundary). Admin-only endpoints — see
interface/http/users.py.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateUserRequest(BaseModel):
    """`POST /api/users` body. `role` is checked against `domain.users.entities.ROLES` in the
    route handler (422 on an unknown role) — same convention as documents.py checking
    `source_type` against `SOURCE_TYPES`."""

    email: str = Field(min_length=1)
    password: str = Field(min_length=8)
    role: str = "consultant"


class UpdateUserRequest(BaseModel):
    """`PATCH /api/users/{id}` body — role change only."""

    role: str
