"""Driving an AWS SSO sign-in from the browser, across three separate requests.

Device authorization is inherently multi-step: begin, wait for a human to approve in another tab,
then choose an account and role. The pieces that carry between those requests — the client
credentials AWS issued and the device code — are held here rather than handed to the browser and
back, because the client secret is a credential and a round trip through JavaScript is a place it
doesn't need to go.

In memory, deliberately. These live minutes, and a pending sign-in is worth exactly nothing after
a restart: the operator starts again. Persisting them would mean storing short-lived secrets and
inventing an expiry sweep for no gain. The cost is that this assumes one backend process, which is
how the app is deployed; a second replica would make a poll land on the wrong one.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.infrastructure.cloud.aws_sso import AwsSsoClient, DeviceAuthorization, SsoToken

#: Long enough for someone to find the browser tab, sign in and approve; short enough that an
#: abandoned attempt doesn't linger. AWS's own device code usually expires first.
_TTL = timedelta(minutes=15)


@dataclass
class PendingSignIn:
    start_url: str
    region: str
    auth: DeviceAuthorization
    expires_at: datetime
    token: SsoToken | None = None
    #: Account ids the operator wants to see. Empty means "everything this sign-in can reach",
    #: which for an organisation of any size is a long list nobody asked for.
    only_accounts: tuple[str, ...] = ()


@dataclass
class SsoConnections:
    """The in-flight sign-ins, keyed by an opaque handle given to the browser."""

    pending: dict[str, PendingSignIn] = field(default_factory=dict)

    def _sweep(self) -> None:
        now = datetime.now(timezone.utc)
        for handle in [h for h, p in self.pending.items() if p.expires_at < now]:
            self.pending.pop(handle, None)

    async def begin(
        self, start_url: str, region: str, only_accounts: tuple[str, ...] = ()
    ) -> tuple[str, PendingSignIn]:
        self._sweep()
        client = AwsSsoClient(start_url=start_url.strip(), region=region.strip())
        auth = await client.begin()
        handle = secrets.token_urlsafe(24)
        entry = PendingSignIn(
            start_url=client.start_url,
            region=client.region,
            auth=auth,
            expires_at=datetime.now(timezone.utc)
            + min(_TTL, timedelta(seconds=auth.expires_in_seconds)),
            only_accounts=only_accounts,
        )
        self.pending[handle] = entry
        return handle, entry

    def get(self, handle: str) -> PendingSignIn | None:
        self._sweep()
        return self.pending.get(handle)

    def client_for(self, entry: PendingSignIn) -> AwsSsoClient:
        return AwsSsoClient(start_url=entry.start_url, region=entry.region)

    def finish(self, handle: str) -> None:
        """Drop the attempt once an integration has been created from it. The device code is
        single-use anyway; keeping it would only leave a credential lying around."""
        self.pending.pop(handle, None)


def visible_roles(entry: PendingSignIn, roles: list) -> list:
    """Narrow the list before it leaves the server.

    Filtering in the browser would be the same screen with the full list still sent over the wire
    and sitting in the network tab — which is not what "only show these accounts" means to the
    person asking for it.
    """
    if not entry.only_accounts:
        return roles
    wanted = set(entry.only_accounts)
    return [r for r in roles if r.account_id in wanted]


def parse_accounts(raw: str | None) -> tuple[str, ...]:
    """Accepts the comma/space/newline-separated list people actually paste."""
    if not raw:
        return ()
    parts = raw.replace(",", " ").replace("\n", " ").split()
    return tuple(dict.fromkeys(p.strip() for p in parts if p.strip()))


def integration_config(entry: PendingSignIn, account_id: str, role_name: str, region: str) -> dict:
    """The non-secret half of an SSO-connected integration.

    `account_id`/`role_name` are stored rather than resolved at use time on purpose: the refresh
    token can mint credentials for every role its owner can reach, and writing down the one this
    integration was connected for is what keeps it to that one.
    """
    return {
        "auth_type": "sso_oidc",
        "sso_start_url": entry.start_url,
        "sso_region": entry.region,
        "account_id": account_id,
        "role_name": role_name,
        "region": region,
    }


def integration_secrets(entry: PendingSignIn) -> dict:
    if entry.token is None or not entry.token.refresh_token:
        raise ValueError("this sign-in has no refresh token yet")
    return {
        "sso_client_id": entry.auth.client_id,
        "sso_client_secret": entry.auth.client_secret,
        "sso_refresh_token": entry.token.refresh_token,
    }
