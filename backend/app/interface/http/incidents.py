"""Incident HTTP controller (SPEC section 6.4).

Parses requests into DTOs, delegates to the ingest use case (writes) or the repository (reads), and
maps domain entities back to response DTOs. No business rules or persistence details live here.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.application.incidents.chat import IncidentChat
from app.application.incidents.ingest import IngestIncident
from app.application.incidents.resolve import ResolveIncident
from app.domain.integrations.entities import TICKETS, Integration
from app.domain.integrations.ports import IntegrationRepository
from app.domain.documents.ports import DocumentRepository
from app.domain.incidents.lanes import LANES
from app.domain.incidents.entities import Analysis, Incident
from app.domain.incidents.ports import ChatRepository, IncidentRepository, TicketClient
from app.domain.shared import UnitOfWork
from app.infrastructure.events import BusProgressReporter, IncidentEventBus
from app.infrastructure.tickets.ado_client import AdoApiError
from app.domain.users.scope import ProjectScope
from app.interface.http.deps import (
    chat_is_supported,
    get_integration_repository,
    get_project_scope,
    get_ado_ticket_client_factory,
    get_chat_repository,
    get_document_repository,
    get_event_bus,
    get_incident_chat,
    get_incident_repository,
    get_ingest_incident,
    get_resolve_incident,
    get_unit_of_work,
    require_role,
    resolve_background_incident_deps,
)
from app.interface.http.dto import mappers
from app.interface.http.dto.request import (
    BulkIncidentRequest,
    ChatMessageRequest,
    IncidentIngestRequest,
    ResolveIncidentRequest,
)
from app.interface.http.dto.response import (
    BulkResultOut,
    ChatMessageOut,
    IncidentCreatedResponse,
    IncidentDetail,
    IncidentRollupOut,
    IncidentSummary,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

router = APIRouter(
    prefix="/api/incidents",
    tags=["incidents"],
    dependencies=[Depends(require_role("admin", "sre", "consultant"))],
)


@router.post(
    "",
    response_model=IncidentCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin", "sre"))],
)
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


@router.post(
    "/{incident_id}/analyze",
    response_model=IncidentCreatedResponse,
    dependencies=[Depends(require_role("admin", "sre"))],
)
async def analyze_incident_now(
    incident_id: uuid.UUID,
    request: Request,
    repo: IncidentRepository = Depends(get_incident_repository),
    bus: IncidentEventBus = Depends(get_event_bus),
    uow: UnitOfWork = Depends(get_unit_of_work),
    scope: ProjectScope = Depends(get_project_scope),
) -> IncidentCreatedResponse:
    """Manually trigger analysis for an incident that hasn't been analyzed yet (status="new") —
    the CloudWatch-alarm auto-ingest path creates incidents this way so alarm-created incidents
    don't spend an LLM call until someone reviews the raw alert and asks for it. Reuses
    `_run_analysis`, the same background-analysis path `POST /api/incidents` schedules."""
    incident = await _visible_incident(repo, incident_id, scope)
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
    scope: ProjectScope = Depends(get_project_scope),
) -> StreamingResponse:
    """Live progress for one incident's analysis (SSE streaming design, decision 2026-07-20).

    Subscribes to the bus BEFORE checking DB status — not after — so a terminal event published
    between the two can never be missed: the queue reference is grabbed first, and `close()` only
    drops the bus's own bookkeeping, never the queue object a subscriber already holds.
    """
    incident_id_str = str(incident_id)
    queue = bus.subscribe(incident_id_str)
    incident = await _visible_incident(repo, incident_id, scope)

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
    scope: ProjectScope = Depends(get_project_scope),
) -> list[ChatMessageOut]:
    await _visible_incident(repo, incident_id, scope)
    messages = await chat.list_messages(incident_id)
    return [mappers.chat_message_out(m) for m in messages]


@router.post(
    "/{incident_id}/chat",
    response_model=ChatMessageOut,
    dependencies=[Depends(require_role("admin", "sre"))],
)
async def send_chat_message(
    incident_id: uuid.UUID,
    body: ChatMessageRequest,
    repo: IncidentRepository = Depends(get_incident_repository),
    incident_chat: IncidentChat = Depends(get_incident_chat),
    chat_capable: bool = Depends(chat_is_supported),
    scope: ProjectScope = Depends(get_project_scope),
) -> ChatMessageOut:
    """Chat about one incident, grounded in its raw context — Claude investigates autonomously
    mid-conversation via the tools in `infrastructure/llm/incident_tools.py` (design spec
    .claude/specs/2026-08-23-incident-chat-design.md).

    Supported on the Anthropic API and the Claude Code subscription; the other providers have no
    tool-calling adapter, and chat without tools would be back to guessing out loud."""
    if not chat_capable:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "Incident chat needs a model profile on the Anthropic API or the Claude Code "
                "subscription — set one on Settings → AI & usage."
            ),
        )
    incident = await _visible_incident(repo, incident_id, scope)
    reply = await incident_chat.send_message(incident, body.message)
    return mappers.chat_message_out(reply)


@router.delete(
    "/{incident_id}/chat",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role("admin", "sre"))],
)
async def reset_incident_chat(
    incident_id: uuid.UUID,
    repo: IncidentRepository = Depends(get_incident_repository),
    chat: ChatRepository = Depends(get_chat_repository),
    uow: UnitOfWork = Depends(get_unit_of_work),
    scope: ProjectScope = Depends(get_project_scope),
) -> None:
    """Start the conversation over.

    A chat session outlives the code that began it: the system prompt naming the investigation
    tools is only sent on a session's first turn, so a conversation started before a tool shipped
    never learns it exists and keeps answering "I have no way to check that". Resetting is the way
    back, and it is the operator's call because it discards the transcript.
    """
    await _visible_incident(repo, incident_id, scope)
    await chat.clear(incident_id)
    await uow.commit()


@router.get("", response_model=list[IncidentSummary])
async def list_incidents(
    repo: IncidentRepository = Depends(get_incident_repository),
    scope: ProjectScope = Depends(get_project_scope),
    service: str | None = None,
    severity: str | None = None,
    incident_status: str | None = Query(default=None, alias="status"),
    lane: str | None = Query(default=None, description=f"one of {list(LANES)}"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[IncidentSummary]:
    """List incidents (open first, then newest), optionally filtered by service / severity /
    status / triage lane."""
    try:
        rows = await repo.list(
            service=service,
            severity=severity,
            status=incident_status,
            lane=lane,
            projects=scope.names,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:  # an unknown lane name
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return [mappers.incident_summary(incident, analysis) for incident, analysis in rows]


#: Bulk analyze is one paid LLM call per id, so it stops well short of the request cap — a page of
#: 25 alarms triaged in one click is a plausible click, and an expensive one.
_MAX_BULK_ANALYZE = 10


@router.post(
    "/bulk",
    response_model=BulkResultOut,
    dependencies=[Depends(require_role("admin", "sre"))],
)
async def bulk_incidents(
    body: BulkIncidentRequest,
    request: Request,
    repo: IncidentRepository = Depends(get_incident_repository),
    resolve: ResolveIncident = Depends(get_resolve_incident),
    bus: IncidentEventBus = Depends(get_event_bus),
    uow: UnitOfWork = Depends(get_unit_of_work),
    scope: ProjectScope = Depends(get_project_scope),
) -> BulkResultOut:
    """Apply one action to several incidents.

    Each id is checked against the caller's project scope individually and skipped if it isn't
    theirs — the same 404-shaped silence a single request gets, except a bulk call reports it as a
    count rather than failing the whole batch, so one stale id in a selection doesn't undo the rest.
    """
    if body.action == "analyze" and len(body.incident_ids) > _MAX_BULK_ANALYZE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"analyze is limited to {_MAX_BULK_ANALYZE} incidents at a time — each one is a "
                "separate AI call"
            ),
        )

    done: list[uuid.UUID] = []
    skipped: list[uuid.UUID] = []
    for incident_id in body.incident_ids:
        incident = await repo.get(incident_id)
        if incident is None or not scope.allows(incident.service):
            skipped.append(incident_id)
            continue
        if body.action == "resolve":
            await resolve.resolve(incident_id, resolution_notes=body.resolution_notes)
        else:
            await repo.set_status(incident_id, "analyzing")
            incident.status = "analyzing"
            await uow.commit()
            bus.open(str(incident.id))
            asyncio.create_task(_run_analysis(request.app, bus, incident))
        done.append(incident_id)
    return BulkResultOut(action=body.action, done=len(done), skipped=len(skipped))


# Declared before `/{incident_id}`: FastAPI matches in definition order, so the other way round
# "rollup" would be parsed as an incident id and 422 on the UUID conversion.
@router.get("/rollup", response_model=IncidentRollupOut)
async def incidents_rollup(
    repo: IncidentRepository = Depends(get_incident_repository),
    scope: ProjectScope = Depends(get_project_scope),
    window_hours: int = Query(default=24, ge=1, le=24 * 30),
    service: str | None = Query(
        default=None,
        description="Narrow every number to one project — the Incidents page's own filter.",
    ),
) -> IncidentRollupOut:
    """Dashboard aggregates. Everything here is counted in SQL over the whole table — the incident
    list this used to be derived from is capped at one page, so those counts stopped being true
    exactly when a busy account needed them most."""
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    return mappers.incident_rollup(
        await repo.rollup(since=since, projects=scope.names, service=service)
    )


@router.get("/{incident_id}", response_model=IncidentDetail)
async def get_incident(
    incident_id: uuid.UUID,
    repo: IncidentRepository = Depends(get_incident_repository),
    documents: DocumentRepository = Depends(get_document_repository),
    scope: ProjectScope = Depends(get_project_scope),
) -> IncidentDetail:
    """Return one incident with its context, analysis, and the evidence chunks it cited."""
    incident = await _visible_incident(repo, incident_id, scope)
    analysis = await repo.latest_analysis(incident_id)
    evidence = (
        await documents.evidence_refs(analysis.evidence_chunk_ids) if analysis else []
    )
    return mappers.incident_detail(incident, analysis, evidence)


@router.post(
    "/{incident_id}/resolve",
    response_model=IncidentDetail,
    dependencies=[Depends(require_role("admin", "sre"))],
)
async def resolve_incident(
    incident_id: uuid.UUID,
    body: ResolveIncidentRequest,
    resolve: ResolveIncident = Depends(get_resolve_incident),
    repo: IncidentRepository = Depends(get_incident_repository),
    documents: DocumentRepository = Depends(get_document_repository),
    scope: ProjectScope = Depends(get_project_scope),
) -> IncidentDetail:
    """Mark an incident resolved. When it has an analysis, that's also saved as a known-issue case
    (source_type="incident") so a future similar incident surfaces it via RAG retrieval — an
    incident with no analysis is simply closed out, nothing to write up."""
    # This route was the one mutation missing its scope guard: a scoped user holding an id could
    # close out another project's incident, and resolving also writes it into the shared knowledge
    # base as a known-issue case. 404, not 403 — a 403 would confirm the incident exists.
    await _visible_incident(repo, incident_id, scope)
    try:
        incident = await resolve.resolve(incident_id, resolution_notes=body.resolution_notes)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    analysis = await repo.latest_analysis(incident_id)
    evidence = (
        await documents.evidence_refs(analysis.evidence_chunk_ids) if analysis else []
    )
    return mappers.incident_detail(incident, analysis, evidence)


@router.delete(
    "/{incident_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role("admin", "sre"))],
)
async def delete_incident(
    incident_id: uuid.UUID,
    repo: IncidentRepository = Depends(get_incident_repository),
    uow: UnitOfWork = Depends(get_unit_of_work),
    scope: ProjectScope = Depends(get_project_scope),
) -> None:
    """Permanently remove an incident — e.g. noise that never needs analysis or a resolution
    write-up. Analyses are removed with it; any known-issue document it already produced (from
    an earlier resolve) is kept but unlinked, so past RAG evidence citing it don't break."""
    await _visible_incident(repo, incident_id, scope)
    await repo.delete(incident_id)
    await uow.commit()


async def _visible_incident(
    repo: IncidentRepository, incident_id: uuid.UUID, scope: ProjectScope
) -> Incident:
    """Load an incident the caller is allowed to see, or 404.

    Out-of-scope is deliberately 404 rather than 403: 403 confirms the incident exists, which is
    itself information a user scoped out of that project shouldn't get. Every per-incident route
    goes through this — reads and writes alike, because "can't see it" must also mean "can't
    resolve, ticket, chat about or delete it".
    """
    incident = await repo.get(incident_id)
    if incident is None or not scope.allows(incident.service):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident not found")
    return incident



_NUMBERED_STEP = re.compile(r"^\d+[.)]\s*(.+)$")


def _as_html_paragraph(text: str) -> str:
    return f"<p>{html.escape(text)}</p>"


def _recommended_action_html(text: str) -> str:
    """Renders numbered "1. foo\\n2. bar" text as an `<ol>`, mirroring the frontend's
    `parseSteps`/`RecommendedAction` treatment — falls back to a plain paragraph when the text
    isn't actually a numbered list (fewer than 2 matching lines)."""
    steps = [
        m.group(1)
        for line in text.split("\n")
        if (m := _NUMBERED_STEP.match(line.strip()))
    ]
    if len(steps) < 2:
        return _as_html_paragraph(text)
    items = "".join(f"<li>{html.escape(step)}</li>" for step in steps)
    return f"<ol>{items}</ol>"


def _user_story_html(service: str, analysis: Analysis) -> str:
    """Opens the ticket as an Agile user story instead of a flat "Summary" label — the summary
    sentence already states what's wrong, so framing it as a want/benefit gives the reader the
    "why this matters" a bare summary doesn't."""
    want = analysis.summary.rstrip(". ")
    return _as_html_paragraph(
        f"As a SRE engineer, I want to fix \"{want}\" so that {service} returns to a healthy state."
    )


def _build_ticket_description(
    service: str, analysis: Analysis, incident_id: uuid.UUID, related_ticket_url: str | None
) -> str:
    """Azure DevOps's description/repro-steps fields are HTML rich text, not plain text — a
    plain "\\n\\n"-joined string collapses into one unbroken paragraph in the ADO UI. Build real
    HTML instead, with each section as its own heading + paragraph (or list, for the numbered
    recommended-action steps), separated by <hr> so the sections don't visually run together."""
    sections = [
        ["<h3>User story</h3>", _user_story_html(service, analysis)],
        ["<h3>Root cause</h3>", _as_html_paragraph(analysis.root_cause)],
        ["<h3>Recommended action</h3>", _recommended_action_html(analysis.recommended_action)],
    ]
    if related_ticket_url:
        sections.append(
            [
                "<h3>Related</h3>",
                _as_html_paragraph(
                    f"This looks like a recurrence of a previously ticketed incident — "
                    f"{related_ticket_url}"
                ),
            ]
        )
    parts = []
    for section in sections:
        if parts:
            parts.append("<hr>")
        parts.extend(section)
    parts.append("<hr>")
    parts.append(f"<p><em>IIM incident: {incident_id}</em></p>")
    return "".join(parts)


@router.post(
    "/{incident_id}/ticket",
    response_model=IncidentDetail,
    dependencies=[Depends(require_role("admin", "sre"))],
)
async def create_incident_ticket(
    incident_id: uuid.UUID,
    repo: IncidentRepository = Depends(get_incident_repository),
    documents: DocumentRepository = Depends(get_document_repository),
    integrations: IntegrationRepository = Depends(get_integration_repository),
    ticket_client_factory: Callable[[Integration], TicketClient] = Depends(
        get_ado_ticket_client_factory
    ),
    uow: UnitOfWork = Depends(get_unit_of_work),
    scope: ProjectScope = Depends(get_project_scope),
) -> IncidentDetail:
    """Create an Azure DevOps work item for a genuinely new/unseen error and record its URL,
    moving the incident to status "ticketed". Which ADO org/project to file into is resolved by
    the incident's own project (`service`) — each internal project (EVP, rxdevs, ...) configures
    its own Azure DevOps destination on the Settings page (`ado_connections`)."""
    incident = await _visible_incident(repo, incident_id, scope)
    if incident.ticket_url:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This incident already has a ticket: {incident.ticket_url}",
        )
    analysis = await repo.latest_analysis(incident_id)
    if analysis is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="incident has no analysis to file a ticket from",
        )
    connection = await integrations.for_project(incident.service, TICKETS)
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"No Azure DevOps project configured for '{incident.service}' — "
                "add one on the Settings page"
            ),
        )
    ticket_client = ticket_client_factory(connection)

    # A recurrence of a known issue (see resolve.py's RAG feedback loop) may already have its own
    # ticket from the earlier occurrence — link the new one to it as "Related" instead of either
    # silently duplicating work or blocking a genuinely new occurrence from getting its own ticket.
    related_ticket_url: str | None = None
    if analysis.known_issue_incident_id is not None:
        known_incident = await repo.get(analysis.known_issue_incident_id)
        if known_incident is not None and known_incident.ticket_url:
            related_ticket_url = known_incident.ticket_url

    # A short, specific identifier (e.g. "ecs-easyrx-prod-external-svc-AlarmLow") reads far better
    # as a ticket title than the full AI summary sentence — same headline already shown on the
    # incident detail page. Azure DevOps also rejects System.Title over 255 chars (TF401324), so
    # this is truncated defensively too even though it's normally well under that.
    headline = mappers.build_headline(incident.context) or analysis.summary
    title = f"[{analysis.severity.upper()}] {incident.service}: {headline}"
    if len(title) > 255:
        title = title[:252] + "..."
    description = _build_ticket_description(
        incident.service, analysis, incident_id, related_ticket_url
    )
    try:
        ticket_url = await ticket_client.create_ticket(
            title, description, related_url=related_ticket_url
        )
    except AdoApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    await repo.set_ticket_url(incident_id, ticket_url)
    await uow.commit()
    incident.ticket_url = ticket_url
    incident.status = "ticketed"

    evidence = await documents.evidence_refs(analysis.evidence_chunk_ids)
    return mappers.incident_detail(incident, analysis, evidence)
