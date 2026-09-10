"""Auth request DTOs (the parse-first boundary)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """`POST /api/auth/login` body."""

    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
