"""Which credentials the log path uses, and how loudly the demo source announces itself.

Logs were the one AWS capability that never consulted `integrations`: the alarm and ECS tools
resolved credentials from the project's integration row, while the log fetcher read
`PROJECT_<SERVICE>_*` env vars and an SSO profile out of the host's `~/.aws`. A deployed instance
with a working `sso_oidc` integration — self-renewing, capability `logs`, enabled — still could not
read a log group, because nothing pointed the fetcher at it.

No database and no AWS: the repository, the credential resolver and the session are all faked.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import pytest

from app.domain.integrations.entities import Integration
from app.infrastructure.logs import factory
from app.infrastructure.logs.cloudwatch_fetcher import CloudWatchLogFetcher
from app.infrastructure.logs.demo_fetcher import DemoLogFetcher

pytestmark = pytest.mark.asyncio


class _Settings:
    demo_logs = False
    aws_region = "ap-southeast-1"
    secret_encryption_key = "unused-by-the-fake-resolver"


SENTINEL_SESSION = object()


def _integration(**config) -> Integration:
    return Integration(
        project="gcm",
        provider="aws",
        env="prod",
        config={"region": "us-east-2", "auth_type": "sso_oidc", **config},
        encrypted_secrets={},
        capabilities=["logs"],
    )


@pytest.fixture
def wired(monkeypatch):
    """Point the factory's lazily-imported DB and credential machinery at fakes."""

    def _wire(integration: Integration | None):
        @asynccontextmanager
        async def _session():
            yield object()

        class _Repo:
            def __init__(self, _db): ...

            async def for_provider(self, service, provider):
                assert provider == "aws"
                return integration

        class _Resolver:
            def __init__(self, _enc): ...

            def resolve(self, _integration):
                return SENTINEL_SESSION

        monkeypatch.setattr("app.infrastructure.db.session.SessionLocal", _session)
        monkeypatch.setattr(
            "app.infrastructure.db.repositories.integrations.SqlAlchemyIntegrationRepository",
            _Repo,
        )
        monkeypatch.setattr(
            "app.infrastructure.cloud.credential_resolver.CredentialResolver", _Resolver
        )
        monkeypatch.setattr("app.infrastructure.security.encryptor.Encryptor", lambda _k: object())

    return _wire


async def test_integration_credentials_are_used_when_one_exists(wired) -> None:
    """The point of the change: the project's own integration, not the host's ~/.aws."""
    wired(_integration())

    fetcher = await factory.resolve_log_fetcher("gcm", _Settings())

    assert isinstance(fetcher, CloudWatchLogFetcher)
    assert fetcher._session is SENTINEL_SESSION
    assert fetcher._profile is None


async def test_integration_region_beats_the_global_default(wired) -> None:
    wired(_integration())

    fetcher = await factory.resolve_log_fetcher("gcm", _Settings())

    assert fetcher._region == "us-east-2"


async def test_falls_back_to_the_global_region_when_the_integration_omits_one(wired) -> None:
    integration = _integration()
    integration.config.pop("region")
    wired(integration)

    fetcher = await factory.resolve_log_fetcher("gcm", _Settings())

    assert fetcher._region == "ap-southeast-1"


async def test_projects_with_no_integration_keep_the_env_path(wired, monkeypatch) -> None:
    """An instance configured before integrations existed must not regress."""
    wired(None)
    marker = object()
    monkeypatch.setattr(factory, "build_log_fetcher", lambda service, settings: marker)

    assert await factory.resolve_log_fetcher("gcm", _Settings()) is marker


# --- the demo source must never be silent ----------------------------------------------------


async def test_demo_logs_short_circuits_before_any_db_lookup(wired) -> None:
    """Checked first, so a demo needs no integration and no account behind the project."""

    class _Demo(_Settings):
        demo_logs = True

    assert isinstance(await factory.resolve_log_fetcher("gcm", _Demo()), DemoLogFetcher)


async def test_handing_out_synthetic_logs_warns_with_the_service_name(caplog) -> None:
    """`DEMO_LOGS=true` returns hard-coded lines about a fictional payment-service OOM for *any*
    log group. Chat passes those to the model as a tool result — the one kind of content it is told
    to trust over its own reasoning — so an unannounced handout is invented evidence wearing a
    tool's authority."""

    class _Demo(_Settings):
        demo_logs = True

    with caplog.at_level(logging.WARNING):
        await factory.resolve_log_fetcher("gcm", _Demo())

    assert any(
        record.levelno == logging.WARNING and "SYNTHETIC" in record.getMessage()
        for record in caplog.records
    ), "the demo fetcher was handed out without a warning"
    assert "gcm" in caplog.text


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_the_sync_path_warns_too(caplog) -> None:
    """`build_log_fetcher` is still reachable; it must not be the quiet way to get fake logs."""

    class _Demo(_Settings):
        demo_logs = True

    with caplog.at_level(logging.WARNING):
        assert isinstance(factory.build_log_fetcher("evp", _Demo()), DemoLogFetcher)

    assert "SYNTHETIC" in caplog.text
    assert "evp" in caplog.text
