"""Unit tests for the in-app AWS SSO sign-in — no network.

This exists so a fresh deployment can connect an AWS account through the web UI, with nothing on
the host: no `~/.aws`, no CLI, no pre-placed profile. The parts worth pinning are the ones a happy
path never exercises — what the browser is allowed to hold, what "not yet" looks like, and that the
stored credential is pinned to the one role it was connected for.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.application.integrations.sso_connect import (
    PendingSignIn,
    SsoConnections,
    integration_config,
    integration_secrets,
)
from app.infrastructure.cloud.aws_sso import DeviceAuthorization, SsoToken

_AUTH = DeviceAuthorization(
    client_id="client-abc",
    client_secret="secret-xyz",
    device_code="device-123",
    verification_uri_complete="https://device.sso.us-east-2.amazonaws.com/?user_code=ABCD-EFGH",
    user_code="ABCD-EFGH",
    interval_seconds=5,
    expires_in_seconds=600,
)


def _entry(token: SsoToken | None = None, expires_in_minutes: int = 10) -> PendingSignIn:
    return PendingSignIn(
        start_url="https://d-9a67082ca9.awsapps.com/start",
        region="us-east-2",
        auth=_AUTH,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes),
        token=token,
    )


def _token() -> SsoToken:
    return SsoToken(
        access_token="access-1",
        refresh_token="refresh-1",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=8),
    )


def test_the_config_records_nothing_about_the_host():
    """The point of the whole flow. A config that named a local profile would be right back to
    needing `~/.aws` on the machine, which a fresh deploy doesn't have."""
    config = integration_config(_entry(), "847659741065", "ReadOnlyAccess", "us-east-2")
    assert config["auth_type"] == "sso_oidc"
    assert "sso_profile_name" not in config
    assert config["sso_start_url"].startswith("https://")


def test_the_chosen_account_and_role_are_written_down():
    """The refresh token can mint credentials for every role its owner can reach. Pinning the pair
    here is what keeps this integration to the one it was connected for — without it, a stored
    token quietly becomes access to the whole organisation."""
    config = integration_config(_entry(), "847659741065", "ReadOnlyAccess", "us-east-2")
    assert (config["account_id"], config["role_name"]) == ("847659741065", "ReadOnlyAccess")


def test_only_the_refresh_token_is_stored():
    """Access tokens expire in hours and are always re-mintable; persisting one would be a second
    credential at rest for no gain."""
    secrets = integration_secrets(_entry(token=_token()))
    assert set(secrets) == {"sso_client_id", "sso_client_secret", "sso_refresh_token"}
    assert "access" not in str(secrets.values())


def test_finishing_before_approval_is_refused():
    with pytest.raises(ValueError, match="no refresh token"):
        integration_secrets(_entry())


async def test_an_expired_attempt_is_forgotten():
    """A pending sign-in is worth nothing once it lapses, and leaving it in memory keeps a device
    code alive past its usefulness."""
    connections = SsoConnections()
    connections.pending["stale"] = _entry(expires_in_minutes=-1)
    connections.pending["fresh"] = _entry()
    assert connections.get("stale") is None
    assert connections.get("fresh") is not None


async def test_finishing_clears_the_attempt():
    connections = SsoConnections()
    connections.pending["h"] = _entry(token=_token())
    connections.finish("h")
    assert connections.get("h") is None


def test_an_unknown_handle_is_simply_absent():
    """Poll answers "expired" rather than raising — the UI calls it on a timer, and a stream of
    exceptions for an ordinary case buries the real ones."""
    assert SsoConnections().get(str(uuid.uuid4())) is None
