"""Signed, stateless admin session tokens — a single shared admin password gates the Settings
page (cloud connections, Claude Code token), not a per-user account system.

Zero extra dependencies: an HMAC-SHA256-signed payload, not a JWT library. `create_admin_token`
issues one after a correct password check; `verify_admin_token` checks the signature and expiry on
every gated request.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

_DEFAULT_TTL_SECONDS = 8 * 60 * 60  # 8 hours


def create_admin_token(secret: str, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> str:
    expires_at = int(time.time()) + ttl_seconds
    payload = f"admin:{expires_at}"
    signature = _sign(payload, secret)
    encoded_payload = base64.urlsafe_b64encode(payload.encode()).decode()
    return f"{encoded_payload}.{signature}"


def verify_admin_token(token: str, secret: str) -> bool:
    try:
        encoded_payload, signature = token.split(".", 1)
        payload = base64.urlsafe_b64decode(encoded_payload.encode()).decode()
        _, expires_at_str = payload.split(":", 1)
        expires_at = int(expires_at_str)
    except (ValueError, UnicodeDecodeError):
        return False

    expected_signature = _sign(payload, secret)
    if not hmac.compare_digest(signature, expected_signature):
        return False
    return time.time() < expires_at


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
