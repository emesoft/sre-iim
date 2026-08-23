"""Project-registry request DTOs (the parse-first boundary)."""

from __future__ import annotations

from pydantic import BaseModel


class ProjectCreateRequest(BaseModel):
    """`POST /api/projects` body."""

    name: str
