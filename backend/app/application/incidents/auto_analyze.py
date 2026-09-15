"""AutoAnalyzeIncidents: triage urgent incidents without waiting for a human to click.

An alarm-created incident is deliberately born unanalyzed (see PollAlarmsJob) — analysis costs an
LLM call, so the poller doesn't spend one on every alarm that fires. The result was that the AI,
which is the product, only ever ran when somebody remembered to press a button, and a busy account
accumulated a backlog nobody triaged.

This closes the gap for the incidents that actually warrant the spend. Two deliberate limits:

- **Only what the provider itself called urgent.** The gate is `context["priority"]` — the alarm's
  own priority, recorded at ingest — not the AI's severity, which doesn't exist until after the
  call this is deciding whether to make. An alarm with no reported priority is never auto-analyzed:
  CloudWatch has no priority concept, so "unknown" means the provider never claimed urgency, and
  guessing on its behalf would spend money on every alarm in the account.
- **A hard cap per run.** One misconfigured alarm flapping a hundred times must cost a handful of
  calls, not a hundred.

Runs as a sweep rather than inline in the poller, which means it also picks up incidents created
by the manual "Refresh" button and by webhooks, and retries nothing it has already paid for.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from app.application.incidents.ingest import IngestIncident
from app.domain.incidents.ports import IncidentRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutoAnalyzeResult:
    analyzed: int
    failed: int


@dataclass
class AutoAnalyzeIncidents:
    incidents: IncidentRepository
    ingest: IngestIncident
    priorities: Sequence[str] = ("critical", "high")
    limit: int = 3

    async def run(self) -> AutoAnalyzeResult:
        if not self.priorities or self.limit <= 0:
            return AutoAnalyzeResult(analyzed=0, failed=0)

        pending = await self.incidents.list_pending_auto_analysis(
            priorities=self.priorities, limit=self.limit
        )
        analyzed = failed = 0
        for incident in pending:
            try:
                await self.ingest.analyze_incident(incident)
                analyzed += 1
            except Exception as exc:  # noqa: BLE001 - one bad incident must not stop the sweep
                # Marked failed, not left as "new": otherwise the next sweep picks the same
                # incident up again and keeps paying for the same failure forever.
                await self.incidents.set_status(incident.id, "failed", error_message=str(exc))
                await self.ingest.uow.commit()
                failed += 1
                logger.warning("auto-analysis failed for incident %s: %s", incident.id, exc)
        if analyzed or failed:
            logger.info("auto-analysis: %d analyzed, %d failed", analyzed, failed)
        return AutoAnalyzeResult(analyzed=analyzed, failed=failed)
