"""User-management request DTOs (the parse-first boundary). Admin-only endpoints — see
interface/http/users.py.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class CreateUserRequest(BaseModel):
    """`POST /api/users` body.

    `group_id` decides both what the account may do and which projects it sees — they're the same
    decision. Omitting it creates a Guest: no permissions, no projects, which is the safe state for
    an account whose password hasn't been handed over yet.
    """

    username: str = Field(min_length=1)
    password: str = Field(min_length=8)
    group_id: uuid.UUID | None = None
    email: str | None = None


class ResetPasswordRequest(BaseModel):
    """`POST /api/users/{id}/password` body — admin sets a new password for the account."""

    new_password: str = Field(min_length=8)
