"""Incident HTTP controller (SPEC section 6.4).

Parses requests into DTOs, delegates to the ingest use case (writes) or the repository (reads), and
maps domain entities back to response DTOs. No business rules or persistence details live here.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.application.incidents.chat import IncidentChat
from app.application.incidents.ingest import IngestIncident
from app.application.incidents.resolve import NoAnalysisToResolveError, ResolveIncident
from app.domain.ado_connections.entities import AdoConnection
from app.domain.ado_connections.ports import AdoConnectionRepository
from app.domain.documents.ports import DocumentRepository
from app.domain.incidents.entities import Incident
from app.domain.incidents.ports import ChatRepository, IncidentRepository, TicketClient
from app.domain.shared import UnitOfWork
from app.infrastructure.config import Settings, get_settings
from app.infrastructure.events import BusProgressReporter, IncidentEventBus
from app.infrastructure.tickets.ado_client import AdoApiError
from app.interface.http.deps import (
    get_ado_connection_repository,
    get_ado_ticket_client_factory,
    get_chat_repository,
    get_document_repository,
    get_event_bus,
    get_incident_chat,
    get_incident_repository,
    get_ingest_incident,
    get_resolve_incident,
    get_unit_of_work,
    resolve_background_incident_deps,
)
from app.interface.http.dto import mappers
from app.interface.http.dto.request import (
    ChatMessageRequest,
    IncidentIngestRequest,
    ResolveIncidentRequest,
)
from app.interface.http.dto.response import (
    ChatMessageOut,
    IncidentCreatedResponse,
    IncidentDetail,
    IncidentSummary,
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
                await deps.ingest.incidents.set_status(
                    incident.id, "failed", error_message=str(exc)
                )
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


@router.post("/{incident_id}/analyze", response_model=IncidentCreatedResponse)
async def analyze_incident_now(
    incident_id: uuid.UUID,
    request: Request,
    repo: IncidentRepository = Depends(get_incident_repository),
    bus: IncidentEventBus = Depends(get_event_bus),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> IncidentCreatedResponse:
    """Manually trigger analysis for an incident that hasn't been analyzed yet (status="new") —
    the CloudWatch-alarm auto-ingest path creates incidents this way so alarm-created incidents
    don't spend an LLM call until someone reviews the raw alert and asks for it. Reuses
    `_run_analysis`, the same background-analysis path `POST /api/incidents` schedules."""
    incident = await repo.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident not found")
    await repo.set_status(incident_id, "analyzing")
    incident.status = "analyzing"
    await uow.commit()

    incident_id_str = str(incident.id)
    bus.open(incident_id_str)
    asyncio.create_task(_run_analysis(request.app, bus, incident))
    return IncidentCreatedResponse(
        incident_id=incident.id,
        status=incident.status,
        stream=f"/api/incidents/{incident.id}/stream",
    )


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


@router.get("/{incident_id}/chat", response_model=list[ChatMessageOut])
async def list_chat_messages(
    incident_id: uuid.UUID,
    repo: IncidentRepository = Depends(get_incident_repository),
    chat: ChatRepository = Depends(get_chat_repository),
) -> list[ChatMessageOut]:
    incident = await repo.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident not found")
    messages = await chat.list_messages(incident_id)
    return [mappers.chat_message_out(m) for m in messages]


@router.post("/{incident_id}/chat", response_model=ChatMessageOut)
async def send_chat_message(
    incident_id: uuid.UUID,
    body: ChatMessageRequest,
    repo: IncidentRepository = Depends(get_incident_repository),
    incident_chat: IncidentChat = Depends(get_incident_chat),
    settings: Settings = Depends(get_settings),
) -> ChatMessageOut:
    """Chat about one incident, grounded in its raw context — Claude can autonomously call a
    fetch_logs tool mid-conversation (design spec .claude/specs/2026-08-23-incident-chat-design.md).
    Only implemented for LLM_PROVIDER=claude_cli — see that spec's "Why claude_cli only" section."""
    if settings.llm_provider != "claude_cli":
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="incident chat requires LLM_PROVIDER=claude_cli",
        )
    incident = await repo.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident not found")
    reply = await incident_chat.send_message(incident, body.message)
    return mappers.chat_message_out(reply)


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


@router.post("/{incident_id}/resolve", response_model=IncidentDetail)
async def resolve_incident(
    incident_id: uuid.UUID,
    body: ResolveIncidentRequest,
    resolve: ResolveIncident = Depends(get_resolve_incident),
    repo: IncidentRepository = Depends(get_incident_repository),
    documents: DocumentRepository = Depends(get_document_repository),
) -> IncidentDetail:
    """Mark an incident resolved and save it as a known-issue case (source_type="incident") so a
    future similar incident surfaces it via the existing RAG retrieval path."""
    try:
        incident = await resolve.resolve(incident_id, resolution_notes=body.resolution_notes)
    except NoAnalysisToResolveError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    analysis = await repo.latest_analysis(incident_id)
    evidence = (
        await documents.evidence_refs(analysis.evidence_chunk_ids) if analysis else []
    )
    return mappers.incident_detail(incident, analysis, evidence)


@router.post("/{incident_id}/ticket", response_model=IncidentDetail)
async def create_incident_ticket(
    incident_id: uuid.UUID,
    repo: IncidentRepository = Depends(get_incident_repository),
    documents: DocumentRepository = Depends(get_document_repository),
    ado_connections: AdoConnectionRepository = Depends(get_ado_connection_repository),
    ticket_client_factory: Callable[[AdoConnection], TicketClient] = Depends(
        get_ado_ticket_client_factory
    ),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> IncidentDetail:
    """Create an Azure DevOps work item for a genuinely new/unseen error and record its URL,
    moving the incident to status "ticketed". Which ADO org/project to file into is resolved by
    the incident's own project (`service`) — each internal project (EVP, rxdevs, ...) configures
    its own Azure DevOps destination on the Settings page (`ado_connections`)."""
    incident = await repo.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident not found")
    analysis = await repo.latest_analysis(incident_id)
    if analysis is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="incident has no analysis to file a ticket from",
        )
    connection = await ado_connections.get_by_project(incident.service)
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"No Azure DevOps project configured for '{incident.service}' — "
                "add one on the Settings page"
            ),
        )
    ticket_client = ticket_client_factory(connection)

    # Azure DevOps rejects System.Title over 255 chars (TF401324) — the AI summary alone can
    # exceed that, so truncate the whole title defensively rather than just the summary part.
    title = f"[{analysis.severity.upper()}] {incident.service}: {analysis.summary}"
    if len(title) > 255:
        title = title[:252] + "..."
    description = (
        f"Root cause: {analysis.root_cause}\n\n"
        f"Recommended action: {analysis.recommended_action}\n\n"
        f"IIM incident: {incident_id}"
    )
    try:
        ticket_url = await ticket_client.create_ticket(title, description)
    except AdoApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    await repo.set_ticket_url(incident_id, ticket_url)
    await uow.commit()
    incident.ticket_url = ticket_url
    incident.status = "ticketed"

    evidence = await documents.evidence_refs(analysis.evidence_chunk_ids)
    return mappers.incident_detail(incident, analysis, evidence)
