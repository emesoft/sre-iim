"""Request DTOs for groups."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class GroupWriteRequest(BaseModel):
    """`POST /api/groups` and `PATCH /api/groups/{id}`.

    Projects are a complete list, not a delta — the admin form edits them as a set, so sending the
    whole thing is what the screen means and leaves no room for drift.
    """

    name: str = Field(min_length=1)
    role: str
    description: str | None = None
    projects: list[str] = Field(default_factory=list)
    #: Optional model profile this group's analyses bill to; null follows the deployment default.
    model_profile_id: str | None = None


class AssignGroupRequest(BaseModel):
    """`PUT /api/users/{id}/group` — null moves the account to Guest (no group, no access)."""

    group_id: uuid.UUID | None = None
