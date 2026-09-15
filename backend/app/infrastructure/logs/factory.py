"""Resolves the right `LogFetcher` adapter for an incident's project (decision: projects can each
live on a different cloud/account — GCM on AWS today, others may move to Azure/GCP later).

Only AWS is wired; a project configured for another cloud raises clearly rather than silently
falling back, so a misconfigured `PROJECT_<SERVICE>_CLOUD` fails loudly instead of quietly hitting
the wrong account.

`DEMO_LOGS=true` short-circuits all of that with a synthetic log source, so the log-search flow can
be demonstrated without any cloud account (`logs/demo_fetcher.py`).
"""

from __future__ import annotations

import asyncio
import logging

from app.domain.incidents.ports import LogFetcher
from app.infrastructure.config import Settings, get_project_config
from app.infrastructure.logs.cloudwatch_fetcher import CloudWatchLogFetcher
from app.infrastructure.logs.demo_fetcher import DemoLogFetcher

logger = logging.getLogger(__name__)


def _demo(service: str) -> LogFetcher:
    """The demo source, but never silently.

    `DEMO_LOGS=true` returns hard-coded lines about a fictional `payment-service` OOM for *any* log
    group name. That is fine for a demo and actively dangerous otherwise: the incident chat hands
    those lines to the model as a tool result, which is the one kind of content the model is told
    to trust over its own reasoning. Asking for `prod-ecs/medusa-store` and being given invented
    evidence, with no marker saying so, is worse than the tool failing outright — so every handout
    is logged at WARNING with the service that asked.
    """
    logger.warning(
        "DEMO_LOGS is on: returning SYNTHETIC log lines for service %r. Any analysis or chat "
        "answer citing these logs is citing invented data, not this project's real log group.",
        service,
    )
    return DemoLogFetcher()


async def resolve_log_fetcher(service: str, settings: Settings) -> LogFetcher:
    """The project's log source, preferring its `integrations` row over host configuration.

    Logs used to be the one AWS capability that never consulted `integrations`: the three alarm and
    ECS tools resolved credentials from the project's integration, while this path read
    `PROJECT_<SERVICE>_*` env vars and an SSO profile out of the host's `~/.aws`. On a deployed
    instance that meant a project with a perfectly good `sso_oidc` integration — self-renewing,
    capability `logs`, enabled — still could not read a log group, because nothing pointed the log
    fetcher at it.

    Falls back to the env/profile path when a project has no AWS integration, so an instance
    configured the old way keeps working.
    """
    if settings.demo_logs:
        return _demo(service)

    # Imported here, not at module scope: this module is imported by the MCP tool subprocess and by
    # the request path, and a top-level DB import would drag the repository layer into both.
    from app.infrastructure.cloud.credential_resolver import CredentialResolver
    from app.infrastructure.db.repositories.integrations import SqlAlchemyIntegrationRepository
    from app.infrastructure.db.session import SessionLocal
    from app.infrastructure.security.encryptor import Encryptor

    async with SessionLocal() as db:
        integration = await SqlAlchemyIntegrationRepository(db).for_provider(service, "aws")
    if integration is None:
        return build_log_fetcher(service, settings)

    config = integration.config or {}
    # Off-thread: resolving an `sso_oidc` integration makes blocking AWS calls (refresh the token,
    # mint role credentials), and the resolver raises rather than blocking the event loop.
    session = await asyncio.to_thread(
        CredentialResolver(Encryptor(settings.secret_encryption_key)).resolve, integration
    )
    return CloudWatchLogFetcher(
        region=config.get("region") or settings.aws_region, session=session
    )


def build_log_fetcher(service: str, settings: Settings) -> LogFetcher:
    """Host-configured log source: `PROJECT_<SERVICE>_*` plus an `~/.aws` profile.

    Prefer `resolve_log_fetcher`, which checks the project's integration first. This remains for
    callers with no event loop and for instances configured before integrations existed.
    """
    # Global demo switch, checked first: it applies to every service, so a demo needs no
    # `PROJECT_<SERVICE>_*` block and no real account behind the incident's project.
    if settings.demo_logs:
        return _demo(service)
    project = get_project_config(service, settings)
    if project.cloud == "aws":
        return CloudWatchLogFetcher(
            region=project.aws_region or settings.aws_region, profile=project.aws_profile
        )
    raise NotImplementedError(
        f"no LogFetcher adapter for cloud={project.cloud!r} (service={service!r})"
    )
