"""Unit tests for CredentialResolver — verifies which boto3.Session kwargs each auth_type builds,
without making a real AWS call."""

from unittest.mock import patch

import pytest

from app.domain.integrations.entities import Integration
from app.infrastructure.cloud.credential_resolver import CredentialResolver
from app.infrastructure.security.encryptor import Encryptor

_KEY = "zH8yV2m3sVW6tG5v9pQwQflR4z1sT8y3lU9wA0b3iF4="


def test_sso_connection_uses_profile_name():
    connection = Integration(
        project="GCM", env="prod", provider="aws",
        config={
            "region": "ap-southeast-1",
            "auth_type": "sso",
            "sso_profile_name": "GCM-Prod-ReadOnlyAccess",
        },
    )
    resolver = CredentialResolver(Encryptor(_KEY))
    with patch("app.infrastructure.cloud.credential_resolver.boto3.Session") as mock_session:
        resolver.resolve(connection)
    mock_session.assert_called_once_with(
        profile_name="GCM-Prod-ReadOnlyAccess", region_name="ap-southeast-1"
    )


def test_access_key_connection_decrypts_and_builds_session():
    encryptor = Encryptor(_KEY)
    connection = Integration(
        project="GCM", env="prod", provider="aws",
        config={"region": "ap-southeast-1", "auth_type": "access_key"},
        encrypted_secrets={
            "access_key_id": encryptor.encrypt("AKIAEXAMPLE"),
            "secret_access_key": encryptor.encrypt("supersecret"),
        },
    )
    resolver = CredentialResolver(encryptor)
    with patch("app.infrastructure.cloud.credential_resolver.boto3.Session") as mock_session:
        resolver.resolve(connection)
    mock_session.assert_called_once_with(
        aws_access_key_id="AKIAEXAMPLE",
        aws_secret_access_key="supersecret",
        region_name="ap-southeast-1",
    )


def test_unknown_auth_type_raises():
    connection = Integration(
        project="GCM", env="prod", provider="aws",
        config={"region": "us-east-1", "auth_type": "bogus"},
    )
    resolver = CredentialResolver(Encryptor(_KEY))
    with pytest.raises(ValueError, match="auth_type"):
        resolver.resolve(connection)


# --- an expired SSO session is the expected end of every sso_oidc connection -------------------


class _InvalidGrantException(Exception):
    """Shaped like botocore's: the class name is what identifies it, there is no shared base."""


def test_expired_refresh_token_says_what_to_do_not_what_boto_called_it():
    """botocore's "InvalidGrantException: Invalid refresh token provided" is accurate and useless:
    the reader cannot tell it means "click Connect with AWS SSO again". Refresh tokens stop working
    when the SSO session policy's limit is reached, so this is a normal end-of-life, not a fault."""
    from app.infrastructure.cloud.credential_resolver import SsoSessionExpired, _expired_sso

    translated = _expired_sso(
        _InvalidGrantException(
            "An error occurred (InvalidGrantException) when calling the CreateToken "
            "operation: Invalid refresh token provided"
        ),
        {"account_id": "847659741065"},
    )

    assert isinstance(translated, SsoSessionExpired)
    assert "847659741065" in str(translated)
    assert "Connect with AWS SSO" in str(translated)


def test_an_unrelated_failure_is_passed_through_untouched():
    """Only the expired grant is narrowed — a network error or a denied role must keep its own
    message, or a real misconfiguration gets reported as "just sign in again"."""
    from app.infrastructure.cloud.credential_resolver import SsoSessionExpired, _expired_sso

    original = RuntimeError("Could not connect to the endpoint URL")

    assert _expired_sso(original, {"account_id": "1"}) is original
    assert not isinstance(_expired_sso(original, {}), SsoSessionExpired)
