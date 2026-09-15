"""Builds a boto3.Session for an AWS integration — SSO reads the host's mounted ~/.aws profile
cache, access_key decrypts the stored key pair. See design spec "Credentials & security".

Config keys read here (`region`, `auth_type`, `sso_profile_name`) and secret names
(`access_key_id`, `secret_access_key`) are the AWS provider's own; they're declared alongside the
adapter in the provider registry so the Settings form and this code agree on one list.
"""

from __future__ import annotations

import asyncio

import boto3

from app.domain.integrations.entities import Integration
from app.infrastructure.cloud.aws_sso import AwsSsoClient, cache_access_token, cached_access_token
from app.infrastructure.security.encryptor import Encryptor


class CredentialResolver:
    def __init__(self, encryptor: Encryptor) -> None:
        self._encryptor = encryptor

    def resolve(self, integration: Integration) -> boto3.Session:
        config = integration.config or {}
        secrets = integration.encrypted_secrets or {}
        region = config.get("region")
        auth_type = config.get("auth_type")

        if auth_type == "sso":
            # The profile is resolved against the host's ~/.aws mounted into the container
            # read-only, so `aws sso login` happens outside this app (see docker-compose.yml).
            return boto3.Session(profile_name=config.get("sso_profile_name"), region_name=region)
        if auth_type == "sso_oidc":
            # Nothing on the host is involved: the refresh token stored with this integration mints
            # an access token, which mints ~1h role credentials for the one account/role chosen
            # when it was connected. That fixed pair is the whole point — the refresh token can
            # reach every role its owner can, and pinning it here keeps this integration to the one
            # it was set up for.
            return self._sso_oidc_session(config, secrets, region)
        if auth_type == "access_key":
            return boto3.Session(
                aws_access_key_id=self._encryptor.decrypt(secrets["access_key_id"]),
                aws_secret_access_key=self._encryptor.decrypt(secrets["secret_access_key"]),
                region_name=region,
            )
        raise ValueError(f"unknown auth_type: {auth_type!r}")

    def _sso_oidc_session(self, config: dict, secrets: dict, region: str | None) -> boto3.Session:
        client = AwsSsoClient(
            start_url=config["sso_start_url"], region=config.get("sso_region") or region
        )
        cache_key = f"{config['sso_start_url']}|{config.get('account_id')}|{config.get('role_name')}"
        access_token = cached_access_token(cache_key)
        if access_token is None:
            try:
                token = _run(
                    client.refresh(
                        self._encryptor.decrypt(secrets["sso_client_id"]),
                        self._encryptor.decrypt(secrets["sso_client_secret"]),
                        self._encryptor.decrypt(secrets["sso_refresh_token"]),
                    )
                )
            except Exception as exc:  # noqa: BLE001 - re-raised below, narrowed or unchanged
                raise _expired_sso(exc, config) from exc
            cache_access_token(cache_key, token)
            access_token = token.access_token
        creds = _run(
            client.credentials(access_token, config["account_id"], config["role_name"])
        )
        return boto3.Session(region_name=region, **creds)


class SsoSessionExpired(RuntimeError):
    """The stored refresh token no longer works, and only a person can fix it.

    Raised instead of botocore's `InvalidGrantException: Invalid refresh token provided`, which is
    accurate and tells the reader nothing about what to do. An AWS SSO refresh token lives as long
    as the SSO instance's session policy allows and then stops, so this is the *expected* end of
    every `sso_oidc` connection — not a malfunction. Reconnecting needs a human to approve in a
    browser, so the message says so rather than reading like something the app could retry.
    """


def _expired_sso(exc: Exception, config: dict) -> Exception:
    """Narrow an expired grant; leave every other failure exactly as it was."""
    if type(exc).__name__ != "InvalidGrantException" and "refresh token" not in str(exc).lower():
        return exc
    account = config.get("account_id") or "this account"
    return SsoSessionExpired(
        f"The AWS SSO session for {account} has expired. Reconnect it on Settings → "
        "Integrations → Connect with AWS SSO — it needs someone to approve the sign-in in a "
        "browser, so it cannot renew itself once the session policy's limit is reached."
    )


def _run(coro):
    """Bridge to the async SSO client from this synchronous resolver.

    `resolve()` is called from boto3-shaped code that already runs in a worker thread (every
    adapter offloads its blocking calls), so there is no running loop here to await on — but the
    SSO client is async because the HTTP layer drives the same calls during sign-in.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "CredentialResolver.resolve() must not be called from the event loop — "
        "it performs blocking AWS calls and belongs in asyncio.to_thread()"
    )
