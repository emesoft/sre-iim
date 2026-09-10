"""Password hashing (bcrypt). Used by application/auth/login.py and application/users/manage.py —
kept out of the domain layer since it's a concrete crypto library, not a business rule.
"""

from __future__ import annotations

import bcrypt


def hash_password(password: str) -> str:
    """Hash a plaintext password for storage. bcrypt generates its own random salt per call, so
    two users with the same password get different hashes."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time check of a plaintext password against a stored bcrypt hash. Returns False
    (never raises) on a malformed/foreign hash, so a corrupted row reads as "wrong password"
    rather than a 500."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False
