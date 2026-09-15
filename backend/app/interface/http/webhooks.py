"""Inbound alert webhooks from external monitoring tools (New Relic today).

No `require_role` gate here — the caller is an external system, not a logged-in user, so it has
no JWT to present. `?secret=` is the only guard (see `Settings.newrelic_webhook_secret`).

Mirrors `PollAlarmsJob`'s CloudWatch convention: the incident is created with status="new" and is
NOT auto-analyzed — an on-call engineer reviews the raw alert and clicks "Analyze with AI" when
they want the LLM call, same reasoning as the module docstring in `poll_alarms.py`.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from app.application.incidents.ingest import IngestIncident
from app.application.webhooks.newrelic import build_newrelic_context
from app.infrastructure.config import Settings, get_settings
from app.interface.http.deps import get_ingest_incident
from app.interface.http.dto.response import IncidentCreatedResponse

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _check_secret(secret: str | None, settings: Settings) -> None:
    if settings.newrelic_webhook_secret and secret != settings.newrelic_webhook_secret:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid webhook secret")


@router.post(
    "/newrelic/{project}",
    response_model=IncidentCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def newrelic_webhook(
    project: str,
    payload: dict = Body(...),
    secret: str | None = Query(default=None),
    env: str | None = Query(default=None),
    ingest: IngestIncident = Depends(get_ingest_incident),
    settings: Settings = Depends(get_settings),
) -> IncidentCreatedResponse:
    """One New Relic alert -> one incident under `project`. Configure this URL as a Webhook
    notification channel in New Relic (Alerts & AI > Notification channels), pointed at
    `POST /api/webhooks/newrelic/<project>?secret=<newrelic_webhook_secret>`."""
    _check_secret(secret, settings)
    context = build_newrelic_context(project, payload, env=env)
    incident = await ingest.create_incident(source="webhook", context=context, status="new")
    return IncidentCreatedResponse(
        incident_id=incident.id,
        status=incident.status,
        stream=f"/api/incidents/{incident.id}/stream",
    )
