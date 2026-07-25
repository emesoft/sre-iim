"""LogFetcher adapters (infrastructure implementations of `domain.incidents.ports.LogFetcher`)."""

from app.infrastructure.logs.cloudwatch_fetcher import CloudWatchLogFetcher

__all__ = ["CloudWatchLogFetcher"]
