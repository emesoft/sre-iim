"""Ports for RAG ingestion and retrieval. Implemented by infrastructure adapters."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Protocol

from app.domain.documents.entities import Document, EmbeddedChunk, EvidenceRef, RetrievedChunk


class Embedder(Protocol):
    """Turns text into embedding vectors (M3: Titan Text Embeddings v2, 1024-dim)."""

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class DocumentRepository(Protocol):
    """Persistence for documents and their chunks."""

    async def add(self, document: Document, chunks: list[EmbeddedChunk]) -> Document: ...

    async def list(self, *, projects: Sequence[str] | None = None) -> list[tuple[Document, int]]:
        """Return each document with its chunk count, newest first."""
        ...

    async def get_with_content(self, document_id: uuid.UUID) -> tuple[Document, list[str]] | None:
        """The document plus its chunks' text, in order. `None` if not found."""
        ...

    async def update(
        self, document_id: uuid.UUID, document: Document, chunks: list[EmbeddedChunk]
    ) -> Document | None:
        """Replace a document's metadata and its entire chunk set (re-chunked/re-embedded by the
        caller). `None` if `document_id` doesn't exist."""
        ...

    async def delete(self, document_id: uuid.UUID) -> None:
        ...

    async def evidence_refs(self, chunk_ids: list[uuid.UUID]) -> list[EvidenceRef]:
        """Resolve chunk ids to (source_type, document title) refs, preserving order."""
        ...


class Retriever(Protocol):
    """Similarity search over indexed chunks (pgvector cosine)."""

    async def search(
        self,
        *,
        query_embedding: list[float],
        service: str | None,
        source_type: str | None = None,
        top_k: int = 6,
        min_similarity: float = 0.0,
    ) -> list[RetrievedChunk]: ...
