"""Cross-domain ports shared by use cases (not specific to one domain)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """A source of the current time (injected so time-dependent logic is testable)."""

    def now(self) -> datetime: ...


class UnitOfWork(Protocol):
    """Transaction boundary owned by the application layer."""

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
