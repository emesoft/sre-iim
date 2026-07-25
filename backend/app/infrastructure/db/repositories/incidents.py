"""SQLAlchemy implementations of the incident domain ports (repository + cache)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.incidents.entities import Analysis, Incident
from app.infrastructure.db.orm import AnalysisCacheRow, AnalysisRow, IncidentRow
from app.infrastructure.db.repositories.mappers import analysis_to_domain, incident_to_domain


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
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple[Incident, Analysis | None]]:
        stmt = (
            select(IncidentRow, AnalysisRow)
            .join(AnalysisRow, AnalysisRow.incident_id == IncidentRow.id, isouter=True)
            .order_by(IncidentRow.created_at.desc())
        )
        if service:
            stmt = stmt.where(IncidentRow.service == service)
        if status:
            stmt = stmt.where(IncidentRow.status == status)
        if severity:
            stmt = stmt.where(AnalysisRow.severity == severity)
        stmt = stmt.limit(limit).offset(offset)

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
            evidence_chunk_ids=list(analysis.evidence_chunk_ids),
            known_issue_incident_id=analysis.known_issue_incident_id,
            known_issue_similarity=analysis.known_issue_similarity,
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

    async def set_status(self, incident_id: uuid.UUID, status: str) -> None:
        row = await self._s.get(IncidentRow, incident_id)
        if row is not None:
            row.status = status

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
