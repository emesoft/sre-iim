"""Mappers: document domain entities -> response DTOs."""

from __future__ import annotations

from app.domain.documents.entities import Document
from app.interface.http.dto.response.document import DocumentDetail, DocumentSummary


def document_summary(document: Document, chunk_count: int) -> DocumentSummary:
    return DocumentSummary(
        id=document.id,
        title=document.title,
        source_type=document.source_type,
        service=document.service,
        tags=list(document.tags),
        chunk_count=chunk_count,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def document_detail(document: Document, chunks: list[str]) -> DocumentDetail:
    return DocumentDetail(
        **document_summary(document, len(chunks)).model_dump(),
        content="\n\n".join(chunks),
    )
