"""Auth response DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field



class UserOut(BaseModel):
    """A user account as returned to clients — never includes `password_hash`."""

    id: uuid.UUID
    username: str
    email: str | None
    role: str
    # local | entra. The UI needs this to hide "Reset password" on an account whose credentials
    # live in the identity provider — see NotALocalAccountError for why setting one is refused.
    auth_provider: str = "local"
    # The group this account belongs to. Null is Guest: no permissions, no projects, the state
    # every new account starts in.
    group_id: uuid.UUID | None = None
    group_name: str | None = None
    # Which projects this user can see, via their group. Admins see everything regardless.
    projects: list[str] = Field(default_factory=list)
    created_at: datetime


class LoginResponse(BaseModel):
    """`POST /api/auth/login` response: a signed JWT access token, valid for a limited time, plus
    the logged-in user's own record (so the frontend doesn't need a second round-trip to /me)."""

    token: str
    user: UserOut


class EntraConfigOut(BaseModel):
    """`GET /api/auth/entra/config` — what the sign-in screen needs to talk to Microsoft."""

    enabled: bool
    tenant_id: str
    client_id: str
