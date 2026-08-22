"""Unit tests for CredentialResolver — verifies which boto3.Session kwargs each auth_type builds,
without making a real AWS call."""

from unittest.mock import patch

import pytest

from app.domain.cloud_connections.entities import CloudConnection
from app.infrastructure.cloud.credential_resolver import CredentialResolver
from app.infrastructure.security.encryptor import Encryptor

_KEY = "zH8yV2m3sVW6tG5v9pQwQflR4z1sT8y3lU9wA0b3iF4="


def test_sso_connection_uses_profile_name():
    connection = CloudConnection(
        project="GCM", env="prod", region="ap-southeast-1", auth_type="sso",
        sso_profile_name="GCM-Prod-ReadOnlyAccess",
    )
    resolver = CredentialResolver(Encryptor(_KEY))
    with patch("app.infrastructure.cloud.credential_resolver.boto3.Session") as mock_session:
        resolver.resolve(connection)
    mock_session.assert_called_once_with(
        profile_name="GCM-Prod-ReadOnlyAccess", region_name="ap-southeast-1"
    )


def test_access_key_connection_decrypts_and_builds_session():
    encryptor = Encryptor(_KEY)
    connection = CloudConnection(
        project="GCM", env="prod", region="ap-southeast-1", auth_type="access_key",
        encrypted_access_key_id=encryptor.encrypt("AKIAEXAMPLE"),
        encrypted_secret_access_key=encryptor.encrypt("supersecret"),
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
    connection = CloudConnection(project="GCM", env="prod", region="us-east-1", auth_type="bogus")
    resolver = CredentialResolver(Encryptor(_KEY))
    with pytest.raises(ValueError, match="auth_type"):
        resolver.resolve(connection)
