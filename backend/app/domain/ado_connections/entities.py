"""Domain entity for per-project Azure DevOps ticket-creation config. Plain dataclass, no ORM/
framework coupling — same convention as domain/cloud_connections/entities.py.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass
class AdoConnection:
    """Which Azure DevOps org/project one internal project's incident tickets get filed into —
    e.g. EVP incidents go to one ADO project, rxdevs incidents to another."""

    project: str
    org: str
    ado_project: str
    encrypted_pat: str
    work_item_type: str = "Bug"
    id: uuid.UUID | None = None
    created_at: datetime | None = None
