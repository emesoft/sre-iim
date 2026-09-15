"""AWS SSO device-authorization sign-in, driven from the app instead of the host.

The existing `auth_type="sso"` reads a profile out of the host's `~/.aws/config`, mounted read-only
into the container. That works on the laptop it was built on and nowhere else: a fresh deploy has
no such file, and because the mount is read-only the container can never renew the token either —
which is why sessions kept expiring and someone had to run `aws sso login` by hand.

This is the same flow `aws sso login` runs internally, with the app playing the part of the CLI:

    RegisterClient          -> a client id/secret, reusable for months
    StartDeviceAuthorization-> a URL the operator approves in their browser
    CreateToken (polled)    -> an access token (~8h) and a refresh token
    ListAccounts/Roles      -> what this person can reach, for them to choose from
    GetRoleCredentials      -> short-lived AWS credentials for the chosen role

The first three take no credentials at all, which is what lets a container with nothing configured
start the process. Only the refresh token is persisted; the credentials it mints last about an hour
and are never stored.

AWS SSO OIDC: https://docs.aws.amazon.com/singlesignon/latest/OIDCAPIReference/
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import boto3
from botocore import UNSIGNED
from botocore.config import Config

#: Registered once per connection attempt and kept with the integration: re-registering on every
#: sign-in would work, but AWS treats each registration as a distinct client and the refresh token
#: is only valid for the client that obtained it.
_CLIENT_NAME = "IIM incident console"
_SCOPES = ["sso:account:access"]
#: The access token is good for hours; renewed slightly early so a long-running poll cycle never
#: starts a call with a token that expires mid-flight.
_REFRESH_MARGIN = timedelta(minutes=5)


class SsoAuthorizationPending(Exception):
    """The operator hasn't approved the request yet — poll again, don't treat it as a failure."""


class SsoAuthorizationExpired(Exception):
    """The device code timed out (the operator never approved). They have to start over."""


@dataclass(frozen=True)
class DeviceAuthorization:
    """What the operator needs in order to approve, and what we need to poll for the result."""

    client_id: str
    client_secret: str
    device_code: str
    #: The URL with the code already embedded — the only thing the UI needs to show as a link.
    verification_uri_complete: str
    user_code: str
    interval_seconds: int
    expires_in_seconds: int


@dataclass(frozen=True)
class SsoToken:
    access_token: str
    refresh_token: str | None
    expires_at: datetime


@dataclass(frozen=True)
class SsoRole:
    account_id: str
    account_name: str
    role_name: str


@dataclass
class AwsSsoClient:
    """One SSO instance (a start URL in a region). Every call is blocking boto3, off-thread."""

    start_url: str
    region: str

    def _oidc(self):
        # Unsigned on purpose: registration and device authorization are pre-credential steps, and
        # signing them would require the very credentials this flow exists to obtain.
        return boto3.client(
            "sso-oidc", region_name=self.region, config=Config(signature_version=UNSIGNED)
        )

    def _sso(self):
        return boto3.client(
            "sso", region_name=self.region, config=Config(signature_version=UNSIGNED)
        )

    async def begin(self) -> DeviceAuthorization:
        return await asyncio.to_thread(self._begin)

    def _begin(self) -> DeviceAuthorization:
        oidc = self._oidc()
        client = oidc.register_client(
            clientName=_CLIENT_NAME, clientType="public", scopes=_SCOPES
        )
        auth = oidc.start_device_authorization(
            clientId=client["clientId"],
            clientSecret=client["clientSecret"],
            startUrl=self.start_url,
        )
        return DeviceAuthorization(
            client_id=client["clientId"],
            client_secret=client["clientSecret"],
            device_code=auth["deviceCode"],
            verification_uri_complete=auth["verificationUriComplete"],
            user_code=auth["userCode"],
            interval_seconds=int(auth.get("interval") or 5),
            expires_in_seconds=int(auth.get("expiresIn") or 600),
        )

    async def poll(self, auth: DeviceAuthorization) -> SsoToken:
        """One attempt. Raises `SsoAuthorizationPending` until the operator approves — the caller
        decides how often to retry, because the polling interval is the UI's concern."""
        return await asyncio.to_thread(self._poll, auth)

    def _poll(self, auth: DeviceAuthorization) -> SsoToken:
        oidc = self._oidc()
        try:
            token = oidc.create_token(
                clientId=auth.client_id,
                clientSecret=auth.client_secret,
                grantType="urn:ietf:params:oauth:grant-type:device_code",
                deviceCode=auth.device_code,
            )
        except oidc.exceptions.AuthorizationPendingException as exc:
            raise SsoAuthorizationPending() from exc
        except oidc.exceptions.SlowDownException as exc:
            # AWS asking us to back off is still "not yet", not an error to surface.
            raise SsoAuthorizationPending() from exc
        except oidc.exceptions.ExpiredTokenException as exc:
            raise SsoAuthorizationExpired() from exc
        return _token_from(token)

    async def refresh(self, client_id: str, client_secret: str, refresh_token: str) -> SsoToken:
        return await asyncio.to_thread(self._refresh, client_id, client_secret, refresh_token)

    def _refresh(self, client_id: str, client_secret: str, refresh_token: str) -> SsoToken:
        token = self._oidc().create_token(
            clientId=client_id,
            clientSecret=client_secret,
            grantType="refresh_token",
            refreshToken=refresh_token,
        )
        # AWS may or may not rotate the refresh token; keep the old one when it doesn't.
        return _token_from(token, fallback_refresh=refresh_token)

    async def roles(self, access_token: str) -> list[SsoRole]:
        return await asyncio.to_thread(self._roles, access_token)

    def _roles(self, access_token: str) -> list[SsoRole]:
        """Every account/role pair this person can reach — the list they pick one from.

        Flattened rather than nested so the UI shows one choice, not two: "which account" followed
        by "which role" is two decisions where the operator only has one in mind.
        """
        sso = self._sso()
        found: list[SsoRole] = []
        for page in sso.get_paginator("list_accounts").paginate(accessToken=access_token):
            for account in page["accountList"]:
                role_pages = sso.get_paginator("list_account_roles").paginate(
                    accessToken=access_token, accountId=account["accountId"]
                )
                for role_page in role_pages:
                    for role in role_page["roleList"]:
                        found.append(
                            SsoRole(
                                account_id=account["accountId"],
                                account_name=account.get("accountName") or account["accountId"],
                                role_name=role["roleName"],
                            )
                        )
        return sorted(found, key=lambda r: (r.account_name.lower(), r.role_name.lower()))

    async def credentials(self, access_token: str, account_id: str, role_name: str) -> dict:
        return await asyncio.to_thread(self._credentials, access_token, account_id, role_name)

    def _credentials(self, access_token: str, account_id: str, role_name: str) -> dict:
        role = self._sso().get_role_credentials(
            accessToken=access_token, accountId=account_id, roleName=role_name
        )["roleCredentials"]
        return {
            "aws_access_key_id": role["accessKeyId"],
            "aws_secret_access_key": role["secretAccessKey"],
            "aws_session_token": role["sessionToken"],
        }


def _token_from(payload: dict, fallback_refresh: str | None = None) -> SsoToken:
    return SsoToken(
        access_token=payload["accessToken"],
        refresh_token=payload.get("refreshToken") or fallback_refresh,
        expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=int(payload.get("expiresIn") or 3600)),
    )


#: Access tokens live in memory only, keyed by integration. Persisting them would store a second
#: credential for no gain — they expire in hours and are always re-mintable from the refresh token,
#: which is the one thing worth keeping. A restart simply costs one extra refresh call.
_TOKEN_CACHE: dict[str, SsoToken] = {}


def cached_access_token(key: str) -> str | None:
    token = _TOKEN_CACHE.get(key)
    if token and token.expires_at - _REFRESH_MARGIN > datetime.now(timezone.utc):
        return token.access_token
    return None


def cache_access_token(key: str, token: SsoToken) -> None:
    _TOKEN_CACHE[key] = token
