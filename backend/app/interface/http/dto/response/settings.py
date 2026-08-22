"""App-settings response DTOs — deliberately omits the secret value entirely, never round-tripped
back to the client (same principle as CloudConnectionOut for AWS access keys)."""

from __future__ import annotations

from pydantic import BaseModel


class SettingStatus(BaseModel):
    """`GET /api/settings/{key}` response: whether a value is configured, never the value itself."""

    is_set: bool
