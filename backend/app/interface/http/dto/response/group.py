"""Response DTO for a group."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class GroupOut(BaseModel):
    id: uuid.UUID
    name: str
    #: admin | sre | consultant — what members may do.
    role: str
    description: str | None = None
    #: Projects this group opens up. Always empty for an admin group, which is unrestricted.
    projects: list[str] = Field(default_factory=list)
    model_profile_id: str | None = None
    member_count: int = 0
    created_at: datetime
