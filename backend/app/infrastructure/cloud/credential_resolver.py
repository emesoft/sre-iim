"""Builds a boto3.Session for a CloudConnection — SSO reads the host's mounted ~/.aws profile
cache, access_key decrypts the stored key pair. See design spec "Credentials & security"."""

from __future__ import annotations

import boto3

from app.domain.cloud_connections.entities import CloudConnection
from app.infrastructure.security.encryptor import Encryptor


class CredentialResolver:
    def __init__(self, encryptor: Encryptor) -> None:
        self._encryptor = encryptor

    def resolve(self, connection: CloudConnection) -> boto3.Session:
        if connection.auth_type == "sso":
            return boto3.Session(
                profile_name=connection.sso_profile_name, region_name=connection.region
            )
        if connection.auth_type == "access_key":
            return boto3.Session(
                aws_access_key_id=self._encryptor.decrypt(connection.encrypted_access_key_id),
                aws_secret_access_key=self._encryptor.decrypt(connection.encrypted_secret_access_key),
                region_name=connection.region,
            )
        raise ValueError(f"unknown auth_type: {connection.auth_type!r}")
