"""Incident HTTP controller (SPEC section 6.4).

Parses requests into DTOs, delegates to the ingest use case (writes) or the repository (reads), and
maps domain entities back to response DTOs. No business rules or persistence details live here.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.application.incidents.ingest import IngestIncident
from app.domain.documents.ports import DocumentRepository
from app.domain.incidents.entities import Incident
from app.domain.incidents.ports import IncidentRepository, LogFetcher
from app.infrastructure.events import BusProgressReporter, IncidentEventBus
from app.interface.http.deps import (
    get_document_repository,
    get_event_bus,
    get_incident_repository,
    get_ingest_incident,
    get_log_fetcher,
    resolve_background_incident_deps,
)
from app.interface.http.dto import mappers
from app.interface.http.dto.request import IncidentIngestRequest, LogSearchRequest
from app.interface.http.dto.response import (
    IncidentCreatedResponse,
    IncidentDetail,
    IncidentSummary,
    LogEventOut,
    LogSearchResult,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


@router.post("", response_model=IncidentCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_incident(
    body: IncidentIngestRequest,
    request: Request,
    ingest: IngestIncident = Depends(get_ingest_incident),
    bus: IncidentEventBus = Depends(get_event_bus),
) -> IncidentCreatedResponse:
    """Create the incident (fast) and schedule its analysis in the background (SSE streaming
    design, decision 2026-07-20). Poll GET or subscribe to the stream for the result."""
    if not body.context.get("service"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="context.service is required",
        )
    incident = await ingest.create_incident(source=body.source, context=body.context)
    incident_id = str(incident.id)
    bus.open(incident_id)
    asyncio.create_task(_run_analysis(request.app, bus, incident))
    return IncidentCreatedResponse(
        incident_id=incident.id,
        status=incident.status,
        stream=f"/api/incidents/{incident.id}/stream",
    )


async def _run_analysis(app: "FastAPI", bus: IncidentEventBus, incident: Incident) -> None:
    """Background task: runs the rest of the ingest flow with its own DB session (the request's
    session is already closed by the time this runs), reporting progress on the bus, and always
    closing the channel when done — success or failure."""
    incident_id = str(incident.id)
    try:
        async with resolve_background_incident_deps(app) as deps:
            reporter = BusProgressReporter(bus, incident_id)
            try:
                analysis = await deps.ingest.analyze_incident(incident, reporter=reporter)
            except Exception as exc:  # noqa: BLE001 - any analyzer failure surfaces as "failed"
                await deps.ingest.incidents.set_status(incident.id, "failed")
                await deps.ingest.uow.commit()
                await bus.publish(incident_id, {"event": "failed", "data": {"message": str(exc)}})
                return
            evidence = await deps.documents.evidence_refs(list(analysis.evidence_chunk_ids))
            detail = mappers.incident_detail(incident, analysis, evidence)
            await bus.publish(
                incident_id,
                {"event": "analyzed", "data": detail.model_dump(mode="json", by_alias=True)},
            )
    finally:
        bus.close(incident_id)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.get("/{incident_id}/stream")
async def stream_incident(
    incident_id: uuid.UUID,
    bus: IncidentEventBus = Depends(get_event_bus),
    repo: IncidentRepository = Depends(get_incident_repository),
    documents: DocumentRepository = Depends(get_document_repository),
) -> StreamingResponse:
    """Live progress for one incident's analysis (SSE streaming design, decision 2026-07-20).

    Subscribes to the bus BEFORE checking DB status — not after — so a terminal event published
    between the two can never be missed: the queue reference is grabbed first, and `close()` only
    drops the bus's own bookkeeping, never the queue object a subscriber already holds.
    """
    incident_id_str = str(incident_id)
    queue = bus.subscribe(incident_id_str)
    incident = await repo.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident not found")

    async def _events() -> AsyncIterator[str]:
        if incident.status in ("analyzed", "failed"):
            if incident.status == "analyzed":
                analysis = await repo.latest_analysis(incident_id)
                evidence = (
                    await documents.evidence_refs(list(analysis.evidence_chunk_ids))
                    if analysis
                    else []
                )
                detail = mappers.incident_detail(incident, analysis, evidence)
                yield _sse("analyzed", detail.model_dump(mode="json", by_alias=True))
            else:
                yield _sse("failed", {"message": "analysis failed"})
            return
        if queue is None:
            yield _sse("failed", {"message": "analysis interrupted"})
            return
        while True:
            event = await queue.get()
            yield _sse(event["event"], event["data"])
            if event["event"] in ("analyzed", "failed"):
                return

    return StreamingResponse(_events(), media_type="text/event-stream")


@router.post("/{incident_id}/logs/search", response_model=LogSearchResult)
async def search_incident_logs(
    incident_id: uuid.UUID,
    body: LogSearchRequest,
    repo: IncidentRepository = Depends(get_incident_repository),
    documents: DocumentRepository = Depends(get_document_repository),
    ingest: IngestIncident = Depends(get_ingest_incident),
    log_fetcher: LogFetcher = Depends(get_log_fetcher),
) -> LogSearchResult:
    """Fetch real log lines for `log_group` via CloudWatch Logs Insights, merge them into the
    incident's context as `sample_logs`, and re-run analysis grounded in the real logs."""
    incident = await repo.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident not found")

    events = await log_fetcher.fetch_logs(
        body.log_group, body.start, body.end, body.filter_pattern
    )
    sample_logs = [{"timestamp": e.timestamp.isoformat(), "message": e.message} for e in events]
    context = {**incident.context, "sample_logs": sample_logs}

    analysis = await ingest.reanalyze_with_context(
        incident, context=context, log_group=body.log_group
    )
    evidence = await documents.evidence_refs(list(analysis.evidence_chunk_ids))
    return LogSearchResult(
        log_group=body.log_group,
        log_events=[LogEventOut(timestamp=e.timestamp, message=e.message) for e in events],
        analysis=mappers.analysis_out(analysis, evidence),
    )


@router.get("", response_model=list[IncidentSummary])
async def list_incidents(
    repo: IncidentRepository = Depends(get_incident_repository),
    service: str | None = None,
    severity: str | None = None,
    incident_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[IncidentSummary]:
    """List incidents (newest first), optionally filtered by service / severity / status."""
    rows = await repo.list(
        service=service,
        severity=severity,
        status=incident_status,
        limit=limit,
        offset=offset,
    )
    return [mappers.incident_summary(incident, analysis) for incident, analysis in rows]


@router.get("/{incident_id}", response_model=IncidentDetail)
async def get_incident(
    incident_id: uuid.UUID,
    repo: IncidentRepository = Depends(get_incident_repository),
    documents: DocumentRepository = Depends(get_document_repository),
) -> IncidentDetail:
    """Return one incident with its context, analysis, and the evidence chunks it cited."""
    incident = await repo.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident not found")
    analysis = await repo.latest_analysis(incident_id)
    evidence = (
        await documents.evidence_refs(analysis.evidence_chunk_ids) if analysis else []
    )
    return mappers.incident_detail(incident, analysis, evidence)
