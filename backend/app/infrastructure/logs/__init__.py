"""LogFetcher adapters (infrastructure implementations of `domain.incidents.ports.LogFetcher`)."""

from app.infrastructure.logs.cloudwatch_fetcher import CloudWatchLogFetcher
from app.infrastructure.logs.factory import build_log_fetcher

__all__ = ["CloudWatchLogFetcher", "build_log_fetcher"]
