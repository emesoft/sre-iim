"""A group: the one thing a person belongs to.

It carries both halves of the answer — the permission level (what you may do) and the projects it
opens up (whose data you may see). They used to be a `role` column and a separate teams table, and
an admin screen showing both could never explain which was which.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

# What a group may do. "guest" is deliberately absent: it isn't a group, it's the state of
# belonging to none — see GUEST_ROLE in domain/users/entities.py.
GROUP_ROLES = ("admin", "sre", "consultant")


@dataclass
class Group:
    name: str
    role: str
    description: str | None = None
    #: Project names this group opens up. Ignored for an admin group, which is unrestricted by
    #: role — listing projects there would imply a limit that isn't enforced.
    projects: tuple[str, ...] = ()
    #: Optional model profile this cohort's analyses bill to. None follows the deployment default.
    model_profile_id: str | None = None
    member_count: int = 0
    id: uuid.UUID | None = None
    created_at: datetime | None = None
