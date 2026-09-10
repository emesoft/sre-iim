"""Login: verify username/password, issue a JWT access token.

Deliberately doesn't distinguish "unknown username" from "wrong password" in the error it raises —
both surface as InvalidCredentialsError (anti-enumeration).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.users.entities import User
from app.domain.users.errors import InvalidCredentialsError
from app.domain.users.ports import UserRepository
from app.infrastructure.security.jwt import create_access_token
from app.infrastructure.security.passwords import verify_password


@dataclass
class Login:
    users: UserRepository
    jwt_secret: str
    jwt_ttl_seconds: int

    async def execute(self, username: str, password: str) -> tuple[User, str]:
        user = await self.users.get_by_username(username)
        # Run verify_password even on a miss (against a dummy hash) so a nonexistent username
        # doesn't return faster than a wrong password — a cheap timing-based enumeration guard.
        password_hash = user.password_hash if user is not None else _DUMMY_HASH
        ok = verify_password(password, password_hash)
        if user is None or not ok:
            raise InvalidCredentialsError()
        token = create_access_token(user, self.jwt_secret, self.jwt_ttl_seconds)
        return user, token


# A real bcrypt hash of an arbitrary password, used only to keep verify_password's cost constant
# on an unknown-email login attempt.
_DUMMY_HASH = "$2b$12$CwTycUXWue0Thq9StjUM0uJ8i6C0MtZ7ntlkTnGdV5UMWl.6QYAn6"
