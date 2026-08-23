"""ADO-connection response DTOs. Deliberately omits the encrypted PAT entirely — the API never
round-trips it, not even masked (same principle as CloudConnectionOut for AWS access keys)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class AdoConnectionOut(BaseModel):
    """One row in `GET /api/ado-connections`."""

    id: uuid.UUID
    project: str
    org: str
    ado_project: str
    work_item_type: str
    created_at: datetime
