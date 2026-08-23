"""Auth response DTOs."""

from __future__ import annotations

from pydantic import BaseModel


class AdminLoginResponse(BaseModel):
    """`POST /api/auth/admin-login` response: a signed session token, valid for a limited time."""

    token: str
