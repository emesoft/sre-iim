"""Auth request DTOs (the parse-first boundary)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AdminLoginRequest(BaseModel):
    """`POST /api/auth/admin-login` body."""

    password: str = Field(min_length=1)
