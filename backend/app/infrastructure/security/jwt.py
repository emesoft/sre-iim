"""Per-user JWT access tokens (replaces the old shared-password HMAC token in admin_auth.py).

Claims: `sub` (user id), `username`, `role`, `exp`. Signed HS256 with `jwt_secret_key` (config.py).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.domain.users.entities import User

_ALGORITHM = "HS256"


class InvalidTokenError(Exception):
    """Raised decoding a missing/malformed/expired/bad-signature token — the caller (deps.py)
    maps this to a 401, never a 500."""


def create_access_token(user: User, secret: str, ttl_seconds: int) -> str:
    if not secret:
        # Refuse to issue: an empty secret makes every token forgeable by anyone who can compute
        # HS256 with an empty key — the signing scheme is public (open-source) code.
        raise ValueError("JWT_SECRET_KEY is not set")
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(claims, secret, algorithm=_ALGORITHM)


def decode_access_token(token: str, secret: str) -> dict:
    """Returns the decoded claims dict (`sub`, `username`, `role`). Raises `InvalidTokenError` on any
    failure — expired, bad signature, malformed — rather than leaking the underlying PyJWT
    exception type into the interface layer."""
    if not secret:
        # Defense in depth: never accept ANY token when the secret is unset, even one an attacker
        # forged directly with an empty-string key (bypassing create_access_token's own guard).
        raise InvalidTokenError("JWT secret is not configured")
    try:
        claims = jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
    try:
        uuid.UUID(claims.get("sub", ""))
    except (ValueError, TypeError) as exc:
        raise InvalidTokenError("token has no valid subject") from exc
    return claims
