"""ORM row -> domain entity mappers. Keeps repositories free of field-copying boilerplate and
ensures no ORM type leaks past the infrastructure layer.
"""

from __future__ import annotations

from app.domain.documents.entities import Document
from app.domain.incidents.entities import Analysis, ChatMessage, ChatSession, Incident
from app.domain.groups.entities import Group
from app.domain.projects.entities import Project
from app.domain.users.entities import GUEST_ROLE, User
from app.infrastructure.db.orm import (
    GroupRow,
    AnalysisRow,
    ChatMessageRow,
    ChatSessionRow,
    DocumentRow,
    IncidentRow,
    ProjectRow,
    UserRow,
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
        occurrence_count=row.occurrence_count,
        previous_incident_id=row.previous_incident_id,
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
        llm_profile=row.llm_profile,
        cached_input_tokens=row.cached_input_tokens,
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


def chat_message_to_domain(row: ChatMessageRow) -> ChatMessage:
    return ChatMessage(
        incident_id=row.incident_id,
        role=row.role,
        content=row.content,
        input_tokens=row.input_tokens,
        cached_input_tokens=row.cached_input_tokens,
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


def project_to_domain(row: ProjectRow) -> Project:
    return Project(
        id=row.id, name=row.name, auto_analyze=row.auto_analyze, created_at=row.created_at
    )


def user_to_domain(row: UserRow) -> User:
    return User(
        id=row.id,
        username=row.username,
        email=row.email,
        password_hash=row.password_hash,
        auth_provider=row.auth_provider,
        external_id=row.external_id,
        role=row.group.role if row.group else GUEST_ROLE,
        group_id=row.group_id,
        group_name=row.group.name if row.group else None,
        group_model_profile_id=row.group.model_profile_id if row.group else None,
        projects=tuple(sorted(p.project for p in row.group.projects)) if row.group else (),
        created_at=row.created_at,
    )


def group_to_domain(row: GroupRow) -> Group:
    return Group(
        id=row.id,
        name=row.name,
        role=row.role,
        description=row.description,
        model_profile_id=row.model_profile_id,
        projects=tuple(sorted(p.project for p in row.projects)),
        created_at=row.created_at,
    )
