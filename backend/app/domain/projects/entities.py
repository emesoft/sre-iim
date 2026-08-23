"""Domain entity for the shared project registry — the canonical list of project names that
CloudConnection/AdoConnection (and any future per-project connection type) reference. Plain
dataclass, no ORM/framework coupling — same convention as domain/ado_connections/entities.py.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Project:
    """One registered internal project name (e.g. 'EVP', 'rxdevs')."""

    name: str
    id: uuid.UUID | None = None
    created_at: datetime | None = None
