"""IngestIncident use case: cache-first analysis, split into two phases (SSE streaming design,
decision 2026-07-20).

`create_incident` persists the incident (status="analyzing") and commits — fast, so the HTTP layer
can return immediately. `analyze_incident` does the rest: fingerprint(context) -> cache lookup
within TTL. HIT copies the stored analysis for this incident with no Analyzer call (reporting a
"cached" stage); MISS runs the Analyzer (passing the reporter through so it can report its own
stages), persists the analysis, and writes the cache row. Each phase commits through the
UnitOfWork. `execute` is a convenience that runs both phases back to back, for callers that don't
need the split (the dev/debug harness, direct unit/integration tests).

This class depends ONLY on domain entities, pure rules, and ports — no framework, DB, or provider
imports — so it is unit-testable with fakes and is the exact seam the RAG/LangGraph analyzers swap
into (a different `Analyzer` implementation), resolved per project by an `AnalyzerSelector`.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from app.domain.incidents.confidence import confidence_to_score
from app.domain.incidents.entities import Analysis, Incident
from app.domain.incidents.fingerprint import fingerprint
from app.domain.incidents.ports import (
    AnalysisCacheRepository,
    AnalyzerSelector,
    Clock,
    ContextEnricher,
    IncidentRepository,
    NullReporter,
    ProgressReporter,
    UnitOfWork,
)


logger = logging.getLogger(__name__)


@dataclass
class IngestIncident:
    incidents: IncidentRepository
    cache: AnalysisCacheRepository
    analyzers: AnalyzerSelector
    clock: Clock
    uow: UnitOfWork
    cache_ttl_seconds: int
    #: Looks up live facts (real metric values, ECS task counts) before analysis. `None` disables
    #: it, and the pipeline still works — just with less to reason from.
    #:
    #: Deliberately has **no default**. It shipped with one, every construction site quietly
    #: omitted it, and the entire evidence feature was dead in production while every test passed:
    #: the tests build this class directly and passed an enricher, the five real call sites went
    #: through factories that didn't. A required argument turns that into a TypeError at startup.
    enricher: ContextEnricher | None

    async def create_incident(
        self,
        *,
        source: str,
        context: dict,
        status: str = "analyzing",
        occurrence_count: int = 1,
        previous_incident_id: uuid.UUID | None = None,
    ) -> Incident:
        """Persist a new incident and commit. No analysis yet.

        `status` defaults to "analyzing" — the caller is expected to trigger analysis right after
        (the manual UI create flow, `POST /api/incidents`). Pass status="new" for a caller that
        wants the incident to wait for an explicit analyze trigger instead (CloudWatch-alarm
        auto-created incidents — see PollAlarmsJob).

        `occurrence_count`/`previous_incident_id` let PollAlarmsJob record that this incident is a
        repeat firing of the same CloudWatch alarm as a prior (now-resolved) incident — every other
        caller leaves them at the defaults (a first-time/non-alarm incident).

        Precondition: `context['service']` is present (validated at the interface boundary).
        """
        incident = await self.incidents.add(
            Incident(
                service=context["service"],
                source=source,
                fingerprint=fingerprint(context),
                context=context,
                status=status,
                occurrence_count=occurrence_count,
                previous_incident_id=previous_incident_id,
            )
        )
        await self.uow.commit()
        return incident

    async def analyze_incident(
        self, incident: Incident, *, reporter: ProgressReporter | None = None
    ) -> Analysis:
        """Cache-first analysis of an already-created incident; persists + commits the result."""
        reporter = reporter or NullReporter()
        fp = incident.fingerprint
        now = self.clock.now()
        cached = await self.cache.get_valid(fp, now)
        if cached is not None:
            await reporter.stage("cached")
            analysis = await self.incidents.add_analysis(
                _copy_analysis(incident.id, cached, cache_state="HIT")
            )
        else:
            # Only on a miss: a cache hit spends no LLM call, so it should spend no API calls
            # gathering evidence for a call that isn't happening either.
            await self._enrich(incident, fp, reporter)
            analyzer = await self.analyzers.for_project(incident.service)
            draft = await analyzer.analyze(incident.context, reporter=reporter)
            analysis = await self.incidents.add_analysis(
                Analysis(
                    incident_id=incident.id,
                    severity=draft.severity,
                    summary=draft.summary,
                    root_cause=draft.root_cause,
                    recommended_action=draft.recommended_action,
                    confidence=confidence_to_score(draft.confidence),
                    cache_state="MISS",
                    model_id=draft.model_id,
                    llm_profile=draft.llm_profile,
                    evidence_chunk_ids=list(draft.evidence_chunk_ids),
                    known_issue_incident_id=draft.known_issue_incident_id,
                    known_issue_similarity=draft.known_issue_similarity,
                    input_tokens=draft.input_tokens,
                    cached_input_tokens=draft.cached_input_tokens,
                    output_tokens=draft.output_tokens,
                )
            )
            await self.cache.put(
                fp, analysis.id, now + timedelta(seconds=self.cache_ttl_seconds)
            )

        await self.incidents.set_status(incident.id, "analyzed")
        incident.status = "analyzed"
        await self.uow.commit()
        return analysis

    async def _enrich(self, incident: Incident, fingerprint_value: str, reporter) -> None:
        """Merge live evidence into the incident's context and persist it.

        Persisted rather than passed straight to the analyzer so the detail view shows the same
        facts the analysis was based on — an analysis citing numbers nobody else can see is not
        reviewable. The fingerprint is deliberately left untouched: it is the cache key for "the
        same problem", and re-deriving it from evidence that changes every minute would mean the
        cache never hits again.
        """
        if self.enricher is None:
            return
        try:
            extra = await self.enricher.enrich(incident.service, incident.context)
        except Exception as exc:  # noqa: BLE001 - evidence is a bonus, triage is not
            # Guarded here and not only inside the adapter: "never raises" is a guarantee this use
            # case depends on, so it enforces it rather than trusting every future implementation
            # to remember. An expired SSO session must cost a section of evidence, not an incident.
            logger.warning("evidence lookup failed for incident %s: %s", incident.id, exc)
            return
        if not extra:
            return
        await reporter.stage("evidence", ", ".join(sorted(extra)))
        incident.context = {**incident.context, **extra}
        await self.incidents.update_context(
            incident.id, context=incident.context, fingerprint=fingerprint_value
        )

    async def execute(
        self, *, source: str, context: dict, reporter: ProgressReporter | None = None
    ) -> tuple[Incident, Analysis]:
        """Convenience: create the incident, then analyze it, in one call."""
        incident = await self.create_incident(source=source, context=context)
        analysis = await self.analyze_incident(incident, reporter=reporter)
        return incident, analysis

    async def reanalyze_with_context(
        self,
        incident: Incident,
        *,
        context: dict,
        log_group: str | None = None,
        reporter: ProgressReporter | None = None,
    ) -> Analysis:
        """Replace an incident's context (e.g. after fetching real CloudWatch log lines) and
        re-run analysis against it. A different context yields a different fingerprint
        (`fingerprint.py`), so this always re-analyzes unless the exact same content was already
        analyzed and is still cached — same cache-first semantics as `analyze_incident`."""
        fp = fingerprint(context)
        await self.incidents.update_context(
            incident.id, context=context, fingerprint=fp, log_group=log_group
        )
        incident.context = context
        incident.fingerprint = fp
        if log_group is not None:
            incident.log_group = log_group
        await self.uow.commit()
        return await self.analyze_incident(incident, reporter=reporter)


def _copy_analysis(incident_id, source: Analysis, *, cache_state: str) -> Analysis:
    """A fresh Analysis for `incident_id` copying a cached analysis's fields.

    `input_tokens`/`output_tokens` are explicitly 0, not copied from `source` — a cache hit makes
    no LLM call, so no tokens are spent this time; copying the original MISS's count would double
    -count it in any usage total."""
    return Analysis(
        incident_id=incident_id,
        severity=source.severity,
        summary=source.summary,
        root_cause=source.root_cause,
        recommended_action=source.recommended_action,
        confidence=source.confidence,
        cache_state=cache_state,
        model_id=source.model_id,
        llm_profile=source.llm_profile,
        evidence_chunk_ids=list(source.evidence_chunk_ids),
        known_issue_incident_id=source.known_issue_incident_id,
        known_issue_similarity=source.known_issue_similarity,
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
    )
