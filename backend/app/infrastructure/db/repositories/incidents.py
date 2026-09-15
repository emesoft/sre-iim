"""SQLAlchemy implementations of the incident domain ports (repository + cache)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import case
from sqlalchemy import delete as sa_delete
from sqlalchemy.orm import aliased
from sqlalchemy import and_, exists, func, or_, select, tuple_, update
from sqlalchemy import true as sa_true
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.incidents.lanes import LANES, statuses_for
from app.domain.incidents.entities import (
    Analysis,
    Incident,
    IncidentRollup,
    NoisyAlarm,
    ProjectRollup,
    UsageByModel,
)
from app.infrastructure.db.orm import (
    ChatMessageRow,
    AnalysisCacheRow,
    AnalysisRow,
    DocumentRow,
    IncidentRow,
    ProjectRow,
    TrackedAlarmRow,
)
from app.infrastructure.db.repositories.mappers import analysis_to_domain, incident_to_domain


_URGENT_SEVERITIES = ("critical", "high")


def _severity_rank():
    """Orders analyses worst-first, with un-analyzed incidents last — an incident nobody has
    triaged yet shouldn't outrank a known critical one when picking what to surface."""
    return case(
        (AnalysisRow.severity == "critical", 0),
        (AnalysisRow.severity == "high", 1),
        (AnalysisRow.severity == "medium", 2),
        (AnalysisRow.severity == "low", 3),
        else_=4,
    )


def _not_superseded():
    """A recurring alarm chains each firing to the last via `previous_incident_id`, and every list
    in the UI shows only the newest link — five firings of one alarm are one thing to work on, not
    five. Counting the raw rows instead produced a "Resolved 19" tab above nine visible rows, which
    is the kind of disagreement that makes people stop trusting the numbers.

    Kept as one predicate so the lists and the counts can't drift into two different opinions about
    what an incident is.
    """
    superseded = aliased(IncidentRow)
    return ~exists(
        select(1).select_from(superseded).where(superseded.previous_incident_id == IncidentRow.id)
    )


def _open_first():
    """Sort key putting still-open incidents ahead of resolved ones, whatever their age.

    Recency alone isn't the right order for a triage list: a just-resolved incident outranking
    everything still waiting is exactly backwards for whoever is on call. It also matters for
    correctness, not just taste — `list()` is capped by `limit`, so a burst of freshly-resolved
    incidents could otherwise fill the whole page and hide open ones from the UI entirely."""
    return case((IncidentRow.status == "resolved", 1), else_=0)


def _latest_analysis_id_subquery():
    """The id of the most recently created `Analysis` for a given incident — a correlated
    subquery joined against `IncidentRow.id`, so `list()`/`list_by_date_range()` return exactly
    one row per incident even when it has been re-analyzed multiple times (e.g. a manual
    "Analyze with AI" retry, or a cache HIT re-run). Joining `AnalysisRow` directly on
    `incident_id` without this would fan out one list row per analysis row — the same ordering
    `latest_analysis()` already uses for a single incident, kept consistent here."""
    return (
        select(AnalysisRow.id)
        .where(AnalysisRow.incident_id == IncidentRow.id)
        .order_by(AnalysisRow.created_at.desc())
        .limit(1)
        .correlate(IncidentRow)
        .scalar_subquery()
    )


class SqlAlchemyIncidentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, incident: Incident) -> Incident:
        row = IncidentRow(
            service=incident.service,
            source=incident.source,
            fingerprint=incident.fingerprint,
            context=incident.context,
            status=incident.status,
            log_group=incident.log_group,
            ticket_url=incident.ticket_url,
            occurrence_count=incident.occurrence_count,
            previous_incident_id=incident.previous_incident_id,
        )
        self._s.add(row)
        await self._s.flush()
        await self._s.refresh(row)
        return incident_to_domain(row)

    async def get(self, incident_id: uuid.UUID) -> Incident | None:
        row = await self._s.get(IncidentRow, incident_id)
        return incident_to_domain(row) if row is not None else None

    async def list(
        self,
        *,
        service: str | None = None,
        severity: str | None = None,
        status: str | None = None,
        lane: str | None = None,
        projects: Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple[Incident, Analysis | None]]:
        stmt = (
            select(IncidentRow, AnalysisRow)
            .join(AnalysisRow, AnalysisRow.id == _latest_analysis_id_subquery(), isouter=True)
            .where(_not_superseded())
            .order_by(_open_first(), IncidentRow.created_at.desc())
        )
        if projects is not None:
            # An empty allow-list is a filter that matches nothing, never "no filter" — that
            # distinction is the whole of the access rule (see domain/users/scope.py).
            stmt = stmt.where(IncidentRow.service.in_(list(projects)))
        if service:
            stmt = stmt.where(IncidentRow.service == service)
        if status:
            stmt = stmt.where(IncidentRow.status == status)
        lane_statuses = statuses_for(lane)
        if lane_statuses is not None:
            # Filtered in SQL, not after paging: a lane has to show all of its own rows, and the
            # page cap applies to the lane rather than to everything that happened to load first.
            stmt = stmt.where(IncidentRow.status.in_(lane_statuses))
        if severity:
            stmt = stmt.where(AnalysisRow.severity == severity)
        stmt = stmt.limit(limit).offset(offset)

        rows = (await self._s.execute(stmt)).all()
        return [
            (incident_to_domain(inc), analysis_to_domain(an) if an is not None else None)
            for inc, an in rows
        ]

    async def list_pending_auto_analysis(
        self, *, priorities: Sequence[str], limit: int
    ) -> list[Incident]:
        stmt = (
            select(IncidentRow)
            .join(AnalysisRow, AnalysisRow.incident_id == IncidentRow.id, isouter=True)
            # A project that has paused automatic triage is excluded here rather than filtered
            # afterwards, so a paused project can't consume the sweep's budget and starve the
            # projects that do want it. An incident whose service isn't a registered project at
            # all (manual ingest with a free-text service) has no switch to honour, so the join is
            # an outer one and a missing row counts as "not opted out".
            .join(ProjectRow, ProjectRow.name == IncidentRow.service, isouter=True)
            .where(
                IncidentRow.status == "new",
                # Never analyzed. Status alone isn't enough of a guard: "new" is also where a
                # re-opened incident could sit, and paying for a second analysis of something
                # already analyzed is exactly what this must not do.
                AnalysisRow.id.is_(None),
                IncidentRow.context["priority"].astext.in_(list(priorities)),
                or_(ProjectRow.auto_analyze.is_(True), ProjectRow.id.is_(None)),
            )
            .order_by(IncidentRow.created_at.desc())
            .limit(limit)
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [incident_to_domain(row) for row in rows]

    async def rollup(
        self,
        *,
        since: datetime,
        noisy_limit: int = 5,
        projects: Sequence[str] | None = None,
        service: str | None = None,
    ) -> IncidentRollup:
        latest_analysis = _latest_analysis_id_subquery()
        open_only = IncidentRow.status != "resolved"
        # Applied to every branch below, so a scoped user's dashboard totals are about their own
        # projects rather than the whole company's.
        visible = and_(
            IncidentRow.service.in_(list(projects)) if projects is not None else sa_true(),
            _not_superseded(),
        )
        # Every project this user could filter to — computed before the page's own filter narrows
        # things, so the dropdown still lists the others once one is picked, and still lists a
        # project whose incidents are all resolved.
        all_projects_stmt = (
            select(IncidentRow.service).where(visible).distinct().order_by(IncidentRow.service)
        )
        if service:
            # The page's own project filter, on top of the access scope. Both apply: a filter the
            # user chose must never widen what they are allowed to see.
            visible = and_(visible, IncidentRow.service == service)

        # Per-project counts. FILTER (WHERE ...) keeps this to one pass instead of a query per
        # metric; "untriaged" is the absence of any analysis row, which is the normal state for an
        # alarm-created incident (polling deliberately doesn't auto-analyze — see PollAlarmsJob).
        per_project = (
            select(
                IncidentRow.service,
                func.count().label("open"),
                func.count()
                .filter(AnalysisRow.severity.in_(_URGENT_SEVERITIES))
                .label("urgent"),
                func.count().filter(AnalysisRow.id.is_(None)).label("untriaged"),
            )
            .join(AnalysisRow, AnalysisRow.id == latest_analysis, isouter=True)
            .where(open_only, visible)
            .group_by(IncidentRow.service)
            .order_by(func.count().desc())
        )

        # The one incident per project worth surfacing: most severe, newest as the tiebreak.
        # DISTINCT ON is Postgres-specific, which this repository already is (pgvector).
        top_per_project = (
            select(IncidentRow.service, IncidentRow.id, IncidentRow.context)
            .join(AnalysisRow, AnalysisRow.id == latest_analysis, isouter=True)
            .where(open_only, visible)
            .distinct(IncidentRow.service)
            .order_by(IncidentRow.service, _severity_rank(), IncidentRow.created_at.desc())
        )

        noisiest = (
            select(
                IncidentRow.service,
                IncidentRow.fingerprint,
                func.count().label("n"),
            )
            .where(IncidentRow.created_at >= since, visible)
            .group_by(IncidentRow.service, IncidentRow.fingerprint)
            .having(func.count() > 1)
            .order_by(func.count().desc())
            .limit(noisy_limit)
        )

        intake = (
            select(func.count())
            .select_from(IncidentRow)
            .where(IncidentRow.created_at >= since, visible)
        )

        # One row per status, folded into lanes below — cheaper and less brittle than four
        # separate counting queries that could drift apart from domain/incidents/lanes.py.
        by_status = (
            select(IncidentRow.status, func.count())
            .where(visible)
            .group_by(IncidentRow.status)
        )

        project_rows = (await self._s.execute(per_project)).all()
        top_rows = {svc: (iid, ctx) for svc, iid, ctx in (await self._s.execute(top_per_project)).all()}
        noisy_rows = (await self._s.execute(noisiest)).all()
        new_last_24h = (await self._s.execute(intake)).scalar_one()
        status_counts = dict((await self._s.execute(by_status)).all())
        all_projects = tuple((await self._s.execute(all_projects_stmt)).scalars().all())
        lanes = {
            lane: sum(status_counts.get(st, 0) for st in statuses)
            for lane, statuses in LANES.items()
        }

        # One representative context per noisy group, for its label — a second small query rather
        # than an aggregate over JSON, since at most `noisy_limit` groups reach this point.
        noisy_context: dict[tuple[str, str], dict] = {}
        if noisy_rows:
            keys = [(svc, fp) for svc, fp, _ in noisy_rows]
            representative = (
                select(IncidentRow.service, IncidentRow.fingerprint, IncidentRow.context)
                .where(tuple_(IncidentRow.service, IncidentRow.fingerprint).in_(keys))
                .distinct(IncidentRow.service, IncidentRow.fingerprint)
                .order_by(
                    IncidentRow.service, IncidentRow.fingerprint, IncidentRow.created_at.desc()
                )
            )
            noisy_context = {
                (svc, fp): ctx for svc, fp, ctx in (await self._s.execute(representative)).all()
            }

        projects = tuple(
            ProjectRollup(
                project=svc,
                open=open_count,
                urgent=urgent,
                untriaged=untriaged,
                top_incident_id=top_rows.get(svc, (None, None))[0],
                top_context=top_rows.get(svc, (None, None))[1],
            )
            for svc, open_count, urgent, untriaged in project_rows
        )
        return IncidentRollup(
            active=sum(p.open for p in projects),
            urgent=sum(p.urgent for p in projects),
            untriaged=sum(p.untriaged for p in projects),
            new_last_24h=new_last_24h,
            lanes=lanes,
            all_projects=all_projects,
            projects=projects,
            noisy=tuple(
                NoisyAlarm(
                    service=svc,
                    fingerprint=fp,
                    count=n,
                    context=noisy_context.get((svc, fp)),
                )
                for svc, fp, n in noisy_rows
            ),
        )

    async def list_by_date_range(
        self,
        start: datetime,
        end: datetime,
        *,
        service: str | None = None,
        projects: Sequence[str] | None = None,
    ) -> list[tuple[Incident, Analysis | None]]:
        stmt = (
            select(IncidentRow, AnalysisRow)
            .join(AnalysisRow, AnalysisRow.id == _latest_analysis_id_subquery(), isouter=True)
            .where(IncidentRow.created_at >= start, IncidentRow.created_at < end)
            .order_by(IncidentRow.created_at.desc())
        )
        if projects is not None:
            stmt = stmt.where(IncidentRow.service.in_(list(projects)))
        if service:
            stmt = stmt.where(IncidentRow.service == service)
        rows = (await self._s.execute(stmt)).all()
        return [
            (incident_to_domain(inc), analysis_to_domain(an) if an is not None else None)
            for inc, an in rows
        ]

    async def add_analysis(self, analysis: Analysis) -> Analysis:
        row = AnalysisRow(
            incident_id=analysis.incident_id,
            severity=analysis.severity,
            summary=analysis.summary,
            root_cause=analysis.root_cause,
            recommended_action=analysis.recommended_action,
            confidence=analysis.confidence,
            cache_state=analysis.cache_state,
            model_id=analysis.model_id,
            llm_profile=analysis.llm_profile,
            cached_input_tokens=analysis.cached_input_tokens,
            evidence_chunk_ids=list(analysis.evidence_chunk_ids),
            known_issue_incident_id=analysis.known_issue_incident_id,
            known_issue_similarity=analysis.known_issue_similarity,
            input_tokens=analysis.input_tokens,
            output_tokens=analysis.output_tokens,
        )
        self._s.add(row)
        await self._s.flush()
        await self._s.refresh(row)
        return analysis_to_domain(row)

    async def latest_analysis(self, incident_id: uuid.UUID) -> Analysis | None:
        row = await self._s.scalar(
            select(AnalysisRow)
            .where(AnalysisRow.incident_id == incident_id)
            .order_by(AnalysisRow.created_at.desc())
            .limit(1)
        )
        return analysis_to_domain(row) if row is not None else None

    async def usage_by_model(self) -> list[UsageByModel]:
        stmt = (
            select(
                AnalysisRow.model_id,
                AnalysisRow.llm_profile,
                func.sum(AnalysisRow.input_tokens).filter(
                    AnalysisRow.cached_input_tokens.is_not(None)
                ),
                func.sum(func.coalesce(AnalysisRow.cached_input_tokens, 0)),
                func.sum(AnalysisRow.input_tokens).filter(
                    AnalysisRow.cached_input_tokens.is_(None)
                ),
                func.sum(AnalysisRow.output_tokens),
                func.count(),
            )
            .where(AnalysisRow.cache_state == "MISS", AnalysisRow.input_tokens.is_not(None))
            .group_by(AnalysisRow.model_id, AnalysisRow.llm_profile)
        )
        # Chat was invisible here: the table aggregated `analyses` only, while an incident
        # conversation is usually the larger share of the spend. Reported as its own row rather
        # than folded into a model's, because a chat message records no model id — claiming one
        # would be inventing it.
        chat = select(
            func.sum(ChatMessageRow.input_tokens).filter(
                ChatMessageRow.cached_input_tokens.is_not(None)
            ),
            func.sum(func.coalesce(ChatMessageRow.cached_input_tokens, 0)),
            func.sum(ChatMessageRow.input_tokens).filter(
                ChatMessageRow.cached_input_tokens.is_(None)
            ),
            func.sum(ChatMessageRow.output_tokens),
            func.count(),
        ).where(ChatMessageRow.input_tokens.is_not(None))

        rows = (await self._s.execute(stmt)).all()
        usage = [
            UsageByModel(
                model_id=model_id,
                llm_profile=profile,
                input_tokens=int(i or 0),
                cached_input_tokens=int(cached or 0),
                unsplit_input_tokens=int(unsplit or 0),
                output_tokens=int(o or 0),
                analyses_count=c,
            )
            for model_id, profile, i, cached, unsplit, o, c in rows
        ]
        chat_in, chat_cached, chat_unsplit, chat_out, chat_count = (
            await self._s.execute(chat)
        ).one()
        if chat_count:
            usage.append(
                UsageByModel(
                    model_id="incident chat",
                    source="chat",
                    input_tokens=int(chat_in or 0),
                    cached_input_tokens=int(chat_cached or 0),
                    unsplit_input_tokens=int(chat_unsplit or 0),
                    output_tokens=int(chat_out or 0),
                    analyses_count=chat_count,
                )
            )
        return usage

    async def reset_stale_analyzing(self, cutoff: datetime) -> int:
        result = await self._s.execute(
            update(IncidentRow)
            .where(IncidentRow.status == "analyzing", IncidentRow.updated_at < cutoff)
            .values(
                status="new",
                error_message=(
                    "Analysis was interrupted (the server restarted while it was running) — "
                    "it has been put back in the queue."
                ),
            )
        )
        return result.rowcount or 0

    async def set_status(
        self, incident_id: uuid.UUID, status: str, *, error_message: str | None = None
    ) -> None:
        row = await self._s.get(IncidentRow, incident_id)
        if row is not None:
            row.status = status
            row.error_message = error_message

    async def set_ticket_url(self, incident_id: uuid.UUID, ticket_url: str) -> None:
        row = await self._s.get(IncidentRow, incident_id)
        if row is not None:
            row.ticket_url = ticket_url
            row.status = "ticketed"

    async def update_context(
        self,
        incident_id: uuid.UUID,
        *,
        context: dict,
        fingerprint: str,
        log_group: str | None = None,
    ) -> None:
        row = await self._s.get(IncidentRow, incident_id)
        if row is not None:
            row.context = context
            row.fingerprint = fingerprint
            if log_group is not None:
                row.log_group = log_group

    async def delete(self, incident_id: uuid.UUID) -> None:
        """Deletes the incident and its analyses. A handful of other tables reference an incident
        without `ON DELETE CASCADE` (chat sessions/messages do cascade, so those need no help
        here) — this clears those dangling references first so removing a noisy/never-analyzed
        incident never leaves an orphaned foreign key behind:
        - `tracked_alarms.incident_id` / `last_incident_id` (the poller's own bookkeeping)
        - another incident's `previous_incident_id` (the recurrence link, see entities.py)
        - another analysis's `known_issue_incident_id` (a past-incident RAG match pointing here)
        - a saved know-issue `documents.incident_id` (only set on resolved incidents, but this
          incident itself was never necessarily the one resolved)
        - `analysis_cache.analysis_id` (the fingerprint cache row for this incident's analysis —
          `analysis_id` is NOT NULL, so unlike the others this can't be nulled out; the cache row
          itself has to go before its analysis can be deleted)
        """
        await self._s.execute(
            update(TrackedAlarmRow)
            .where(TrackedAlarmRow.incident_id == incident_id)
            .values(incident_id=None)
        )
        await self._s.execute(
            update(TrackedAlarmRow)
            .where(TrackedAlarmRow.last_incident_id == incident_id)
            .values(last_incident_id=None)
        )
        await self._s.execute(
            update(IncidentRow)
            .where(IncidentRow.previous_incident_id == incident_id)
            .values(previous_incident_id=None)
        )
        await self._s.execute(
            update(AnalysisRow)
            .where(AnalysisRow.known_issue_incident_id == incident_id)
            .values(known_issue_incident_id=None)
        )
        await self._s.execute(
            update(DocumentRow).where(DocumentRow.incident_id == incident_id).values(incident_id=None)
        )
        analysis_ids = (
            await self._s.execute(
                select(AnalysisRow.id).where(AnalysisRow.incident_id == incident_id)
            )
        ).scalars().all()
        if analysis_ids:
            await self._s.execute(
                sa_delete(AnalysisCacheRow).where(AnalysisCacheRow.analysis_id.in_(analysis_ids))
            )
        await self._s.execute(sa_delete(AnalysisRow).where(AnalysisRow.incident_id == incident_id))
        row = await self._s.get(IncidentRow, incident_id)
        if row is not None:
            await self._s.delete(row)
        await self._s.flush()


class SqlAlchemyAnalysisCacheRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_valid(self, fingerprint: str, now: datetime) -> Analysis | None:
        cache = await self._s.scalar(
            select(AnalysisCacheRow).where(
                AnalysisCacheRow.fingerprint == fingerprint,
                AnalysisCacheRow.expires_at > now,
            )
        )
        if cache is None:
            return None
        row = await self._s.get(AnalysisRow, cache.analysis_id)
        return analysis_to_domain(row) if row is not None else None

    async def put(self, fingerprint: str, analysis_id: uuid.UUID, expires_at: datetime) -> None:
        existing = await self._s.get(AnalysisCacheRow, fingerprint)
        if existing is None:
            self._s.add(
                AnalysisCacheRow(
                    fingerprint=fingerprint, analysis_id=analysis_id, expires_at=expires_at
                )
            )
        else:
            existing.analysis_id = analysis_id
            existing.expires_at = expires_at
