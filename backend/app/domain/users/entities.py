"""Domain entity for a per-user account. Plain dataclass, no ORM/framework coupling — same
convention as domain/projects/entities.py.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

# Kept as a plain string (not an Enum) so the domain layer stays framework-agnostic and the DB
# column is a simple TEXT — same convention as Incident.status / Document.source_type.
ROLES = ("admin", "sre", "consultant")


@dataclass
class User:
    """One per-user account: username (login identity) + bcrypt password hash + role. `email` is
    optional, purely informational — it is no longer used for login or required to be unique."""

    username: str
    password_hash: str
    role: str = "consultant"
    email: str | None = None
    id: uuid.UUID | None = None
    created_at: datetime | None = None
