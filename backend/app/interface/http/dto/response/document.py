"""Document response DTOs — pure serialization schemas (mapping lives in dto/mappers)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class DocumentCreatedResponse(BaseModel):
    """`POST /api/documents` response: the stored document id and how many chunks were indexed."""

    document_id: uuid.UUID
    chunks: int


class DocumentSummary(BaseModel):
    """One row in `GET /api/documents`."""

    id: uuid.UUID
    title: str
    source_type: str
    service: str | None
    tags: list[str]
    chunk_count: int
    created_at: datetime
    updated_at: datetime


class DocumentDetail(DocumentSummary):
    """`GET /api/documents/{id}`: the summary fields plus the full indexed text."""

    content: str
