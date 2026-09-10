"""Auth response DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class UserOut(BaseModel):
    """A user account as returned to clients — never includes `password_hash`."""

    id: uuid.UUID
    email: str
    role: str
    created_at: datetime


class LoginResponse(BaseModel):
    """`POST /api/auth/login` response: a signed JWT access token, valid for a limited time, plus
    the logged-in user's own record (so the frontend doesn't need a second round-trip to /me)."""

    token: str
    user: UserOut
