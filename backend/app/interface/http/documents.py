"""Knowledge-document HTTP controller (RAG ingest + list; SPEC section 6.4).

Parses the request, validates `source_type` at the boundary, delegates to the ingest use case, and
maps domain entities to response DTOs. Retrieval is internal (used by the M3 analysis graph), so it
has no public endpoint here. `reindex` is out of scope for this slice (needs the raw-file store).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.application.documents.ingest import EmptyDocumentError, IngestDocument
from app.domain.documents.entities import SOURCE_TYPES
from app.domain.documents.ports import DocumentRepository
from app.interface.http.deps import get_document_repository, get_ingest_document
from app.interface.http.dto import mappers
from app.interface.http.dto.request import DocumentIngestRequest
from app.interface.http.dto.response import DocumentCreatedResponse, DocumentDetail, DocumentSummary

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("", response_model=DocumentCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_document(
    body: DocumentIngestRequest,
    ingest: IngestDocument = Depends(get_ingest_document),
) -> DocumentCreatedResponse:
    """Ingest one knowledge document: chunk + embed + store. Returns the id and chunk count."""
    if body.source_type not in SOURCE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"source_type must be one of {list(SOURCE_TYPES)}",
        )
    try:
        document, chunks = await ingest.execute(
            title=body.title,
            source_type=body.source_type,
            service=body.service,
            tags=body.tags,
            content=body.content,
        )
    except EmptyDocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return DocumentCreatedResponse(document_id=document.id, chunks=chunks)


@router.get("", response_model=list[DocumentSummary])
async def list_documents(
    repo: DocumentRepository = Depends(get_document_repository),
) -> list[DocumentSummary]:
    """List indexed documents with their chunk counts (newest first)."""
    rows = await repo.list()
    return [mappers.document_summary(document, count) for document, count in rows]


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: uuid.UUID,
    repo: DocumentRepository = Depends(get_document_repository),
) -> DocumentDetail:
    """One document's full indexed text, chunks rejoined in order."""
    result = await repo.get_with_content(document_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    document, chunks = result
    return mappers.document_detail(document, chunks)
