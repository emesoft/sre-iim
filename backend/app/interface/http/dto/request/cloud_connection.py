"""Cloud-connection request DTOs (the parse-first boundary)."""

from __future__ import annotations

from pydantic import BaseModel


class CloudConnectionCreateRequest(BaseModel):
    """`POST /api/cloud-connections` body."""

    project: str
    env: str
    region: str
    auth_type: str  # sso | access_key (validated at the controller)
    sso_profile_name: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
