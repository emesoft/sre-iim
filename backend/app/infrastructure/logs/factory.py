"""Resolves the right `LogFetcher` adapter for an incident's project (decision: projects can each
live on a different cloud/account — GCM on AWS today, others may move to Azure/GCP later).

Only AWS is wired; a project configured for another cloud raises clearly rather than silently
falling back, so a misconfigured `PROJECT_<SERVICE>_CLOUD` fails loudly instead of quietly hitting
the wrong account.

`DEMO_LOGS=true` short-circuits all of that with a synthetic log source, so the log-search flow can
be demonstrated without any cloud account (`logs/demo_fetcher.py`).
"""

from __future__ import annotations

from app.domain.incidents.ports import LogFetcher
from app.infrastructure.config import Settings, get_project_config
from app.infrastructure.logs.cloudwatch_fetcher import CloudWatchLogFetcher
from app.infrastructure.logs.demo_fetcher import DemoLogFetcher


def build_log_fetcher(service: str, settings: Settings) -> LogFetcher:
    # Global demo switch, checked first: it applies to every service, so a demo needs no
    # `PROJECT_<SERVICE>_*` block and no real account behind the incident's project.
    if settings.demo_logs:
        return DemoLogFetcher()
    project = get_project_config(service, settings)
    if project.cloud == "aws":
        return CloudWatchLogFetcher(
            region=project.aws_region or settings.aws_region, profile=project.aws_profile
        )
    raise NotImplementedError(
        f"no LogFetcher adapter for cloud={project.cloud!r} (service={service!r})"
    )
