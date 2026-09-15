"""Verifies Microsoft Entra ID (Azure AD) ID tokens.

The browser gets an ID token from Entra and posts it here; this module decides whether to believe
it. Everything that matters is in the checks below — an ID token is only evidence of identity if
all of them hold:

- **Signature**, against Microsoft's published keys for this tenant (fetched from the JWKS
  endpoint, cached by PyJWKClient, re-fetched when Entra rotates a key).
- **`aud` == our client id.** Without this, a token Entra issued for a *different* application
  would be accepted here — the signature would be perfectly valid, just meant for someone else.
- **`iss` and `tid` == our tenant.** Single-tenant app: an account from any other Entra directory
  in the world must not log in, even though Microsoft signed its token too.
- **`exp` / `nbf`**, enforced by PyJWT.

The JWKS fetch is blocking (urllib inside PyJWT), so it runs in a thread rather than stalling the
event loop while Microsoft answers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache

import jwt
from jwt import PyJWKClient

_ALGORITHMS = ["RS256"]


class EntraTokenError(Exception):
    """The token isn't usable — wrong audience, wrong tenant, expired, bad signature. The caller
    maps this to a 401; the message is for logs, not for the browser."""


@dataclass(frozen=True)
class EntraIdentity:
    """The parts of a verified token this application acts on."""

    external_id: str  # `oid` — stable across username/email changes
    username: str
    email: str | None
    name: str | None


@lru_cache(maxsize=4)
def _jwk_client(tenant_id: str) -> PyJWKClient:
    # Cached: each instance keeps its own key cache, so a fresh client per request would re-fetch
    # Microsoft's key set on every single login.
    return PyJWKClient(f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys")


def _verify_sync(token: str, tenant_id: str, client_id: str) -> dict:
    try:
        signing_key = _jwk_client(tenant_id).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=_ALGORITHMS,
            audience=client_id,
            issuer=f"https://login.microsoftonline.com/{tenant_id}/v2.0",
        )
    except Exception as exc:  # noqa: BLE001 - every failure mode is "don't trust this token"
        raise EntraTokenError(str(exc)) from exc

    # Belt and braces over the issuer check: `tid` is the directory the account actually lives in.
    if claims.get("tid") != tenant_id:
        raise EntraTokenError("token was issued for a different Entra tenant")
    return claims


async def verify_id_token(token: str, *, tenant_id: str, client_id: str) -> EntraIdentity:
    if not tenant_id or not client_id:
        raise EntraTokenError("Entra login is not configured")
    claims = await asyncio.to_thread(_verify_sync, token, tenant_id, client_id)

    external_id = claims.get("oid")
    if not external_id:
        raise EntraTokenError("token has no `oid` claim")
    # `preferred_username` is normally the UPN/email. It is NOT a safe key to store an account
    # against (Entra lets it change, and it isn't guaranteed unique) — hence `oid` above — but it
    # is the right thing to *display* and to seed the local username from.
    username = claims.get("preferred_username") or claims.get("email") or external_id
    return EntraIdentity(
        external_id=external_id,
        username=username,
        email=claims.get("email") or (username if "@" in username else None),
        name=claims.get("name"),
    )
