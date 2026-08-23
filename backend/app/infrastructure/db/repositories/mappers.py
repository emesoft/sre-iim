"""ORM row -> domain entity mappers. Keeps repositories free of field-copying boilerplate and
ensures no ORM type leaks past the infrastructure layer.
"""

from __future__ import annotations

from app.domain.cloud_connections.entities import CloudConnection, TrackedAlarm
from app.domain.documents.entities import Document
from app.domain.incidents.entities import Analysis, ChatMessage, ChatSession, Incident
from app.infrastructure.db.orm import (
    AnalysisRow,
    ChatMessageRow,
    ChatSessionRow,
    DocumentRow,
    IncidentRow,
)


def incident_to_domain(row: IncidentRow) -> Incident:
    return Incident(
        service=row.service,
        source=row.source,
        fingerprint=row.fingerprint,
        context=row.context,
        status=row.status,
        log_group=row.log_group,
        ticket_url=row.ticket_url,
        error_message=row.error_message,
        id=row.id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def analysis_to_domain(row: AnalysisRow) -> Analysis:
    return Analysis(
        incident_id=row.incident_id,
        severity=row.severity,
        summary=row.summary,
        root_cause=row.root_cause,
        recommended_action=row.recommended_action,
        confidence=float(row.confidence) if row.confidence is not None else None,
        cache_state=row.cache_state,
        model_id=row.model_id,
        evidence_chunk_ids=list(row.evidence_chunk_ids or []),
        known_issue_incident_id=row.known_issue_incident_id,
        known_issue_similarity=(
            float(row.known_issue_similarity) if row.known_issue_similarity is not None else None
        ),
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        id=row.id,
        created_at=row.created_at,
    )


def document_to_domain(row: DocumentRow) -> Document:
    return Document(
        title=row.title,
        source_type=row.source_type,
        service=row.service,
        tags=list(row.tags or []),
        incident_id=row.incident_id,
        id=row.id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def cloud_connection_to_domain(row) -> CloudConnection:
    return CloudConnection(
        id=row.id,
        project=row.project,
        env=row.env,
        cloud=row.cloud,
        region=row.region,
        auth_type=row.auth_type,
        sso_profile_name=row.sso_profile_name,
        encrypted_access_key_id=row.encrypted_access_key_id,
        encrypted_secret_access_key=row.encrypted_secret_access_key,
        last_poll_at=row.last_poll_at,
        last_poll_status=row.last_poll_status,
        last_poll_error=row.last_poll_error,
        last_poll_alarm_count=row.last_poll_alarm_count,
        created_at=row.created_at,
    )


def tracked_alarm_to_domain(row) -> TrackedAlarm:
    return TrackedAlarm(
        id=row.id,
        connection_id=row.connection_id,
        alarm_arn=row.alarm_arn,
        alarm_name=row.alarm_name,
        last_state=row.last_state,
        incident_id=row.incident_id,
        updated_at=row.updated_at,
    )


def chat_message_to_domain(row: ChatMessageRow) -> ChatMessage:
    return ChatMessage(
        incident_id=row.incident_id,
        role=row.role,
        content=row.content,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        id=row.id,
        created_at=row.created_at,
    )


def chat_session_to_domain(row: ChatSessionRow) -> ChatSession:
    return ChatSession(
        incident_id=row.incident_id,
        claude_session_id=row.claude_session_id,
        created_at=row.created_at,
    )
