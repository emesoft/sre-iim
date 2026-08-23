"""Project-registry response DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class ProjectOut(BaseModel):
    """One row in `GET /api/projects`."""

    id: uuid.UUID
    name: str
    created_at: datetime
