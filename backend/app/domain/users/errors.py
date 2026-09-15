"""Domain-level errors for user accounts / login. The application layer raises these after
translating a database constraint violation (unique username) or a failed credential check — this
module itself stays framework-agnostic, no SQLAlchemy/bcrypt/jwt imports.
"""

from __future__ import annotations

import uuid


class UserNotFoundError(Exception):
    """Raised looking up, updating, or deleting a user id that doesn't exist."""

    def __init__(self, user_id: uuid.UUID) -> None:
        super().__init__(f"user {user_id} not found")
        self.user_id = user_id


class UsernameTakenError(Exception):
    """Raised creating a user whose username already exists. Username is now the unique login
    identity — email is optional/free-form and no longer enforced unique."""

    def __init__(self, username: str) -> None:
        super().__init__(f"a user with username '{username}' already exists")
        self.username = username


class InvalidCredentialsError(Exception):
    """Raised on login with an unknown username or a wrong password. Deliberately doesn't say
    which (username vs. password) — same anti-enumeration convention as most login flows."""

    def __init__(self) -> None:
        super().__init__("invalid username or password")


class LastAdminError(Exception):
    """Raised demoting or deleting the only remaining admin.

    Without this guard the system can be locked into a state no one can administer: the last admin
    demotes themselves with the role dropdown, and from then on every admin-only route 403s for
    everybody, with no way back in through the app at all.
    """

    def __init__(self, action: str) -> None:
        super().__init__(
            f"cannot {action} the last admin — promote another user to admin first, "
            "otherwise nobody could manage users, projects or integrations"
        )


class NotALocalAccountError(Exception):
    """Raised setting a password on an account whose credentials live in an identity provider.

    An Entra-provisioned account deliberately has no local password. Giving it one would create a
    second way in that bypasses single sign-on — and with it the tenant's MFA and conditional
    access — which is precisely what SSO exists to prevent.
    """

    def __init__(self, username: str, provider: str) -> None:
        super().__init__(
            f"'{username}' signs in with {provider}; its password is managed there, not here"
        )
        self.username = username
        self.provider = provider
