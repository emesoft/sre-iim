"""Project-registry response DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class ProjectOut(BaseModel):
    """One row in `GET /api/projects`."""

    id: uuid.UUID
    name: str
    # Whether urgent alarms in this project get triaged automatically (per-project switch over
    # the global AUTO_ANALYZE_* settings).
    auto_analyze: bool = True
    created_at: datetime
