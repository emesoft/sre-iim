"""Domain-level errors for user accounts / login. The application layer raises these after
translating a database constraint violation (unique email) or a failed credential check — this
module itself stays framework-agnostic, no SQLAlchemy/bcrypt/jwt imports.
"""

from __future__ import annotations

import uuid


class UserNotFoundError(Exception):
    """Raised looking up, updating, or deleting a user id that doesn't exist."""

    def __init__(self, user_id: uuid.UUID) -> None:
        super().__init__(f"user {user_id} not found")
        self.user_id = user_id


class EmailTakenError(Exception):
    """Raised creating a user whose email already exists."""

    def __init__(self, email: str) -> None:
        super().__init__(f"a user with email '{email}' already exists")
        self.email = email


class InvalidCredentialsError(Exception):
    """Raised on login with an unknown email or a wrong password. Deliberately doesn't say which
    (email vs. password) — same anti-enumeration convention as most login flows."""

    def __init__(self) -> None:
        super().__init__("invalid email or password")
