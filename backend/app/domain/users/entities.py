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

# The role of an account that belongs to no group. It is not a group and cannot be assigned — it's
# what "not set up yet" resolves to, and no route accepts it, so a brand-new account can read
# nothing until an admin puts it in a group.
GUEST_ROLE = "guest"


@dataclass
class User:
    """One per-user account: username (login identity) + bcrypt password hash + the group it
    belongs to. `role` and `projects` are read off that group, never stored here, so the two can't
    drift. `email` is optional, purely informational — not used for login, not unique."""

    username: str
    # None for an account whose identity provider holds the credential (auth_provider != "local").
    password_hash: str | None
    # Derived from the group, never stored on the account — that's why there's no setter for it.
    # GUEST_ROLE when the account belongs to no group.
    role: str = GUEST_ROLE
    group_id: uuid.UUID | None = None
    group_name: str | None = None
    #: The group's own model profile, if it has one — see infrastructure/llm/factory.py.
    group_model_profile_id: str | None = None
    #: Projects the group opens up. Empty for an admin, who is unrestricted by role instead.
    projects: tuple[str, ...] = ()
    email: str | None = None
    auth_provider: str = "local"  # local | entra
    # The provider's own stable id for this user (Entra `oid`), None for local accounts.
    external_id: str | None = None
    id: uuid.UUID | None = None
    created_at: datetime | None = None
