"""IngestDocument use case: split -> embed -> store (RAG slice 1).

Depends only on domain (chunking rule + ports). No framework/DB/provider imports, so it is
unit-testable with a fake embedder and repository.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.documents.chunking import DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP, chunk_markdown
from app.domain.documents.entities import Document, EmbeddedChunk
from app.domain.documents.ports import DocumentRepository, Embedder
from app.domain.shared import UnitOfWork


class EmptyDocumentError(ValueError):
    """The document produced no chunks (empty/whitespace content)."""


async def _chunk_and_embed(
    embedder: Embedder, content: str, chunk_size: int, overlap: int
) -> list[EmbeddedChunk]:
    texts = chunk_markdown(content, chunk_size, overlap)
    if not texts:
        raise EmptyDocumentError("document content produced no chunks")
    vectors = await embedder.embed_documents(texts)
    return [
        EmbeddedChunk(index=i, content=text, embedding=vector)
        for i, (text, vector) in enumerate(zip(texts, vectors))
    ]


@dataclass
class IngestDocument:
    documents: DocumentRepository
    embedder: Embedder
    uow: UnitOfWork
    chunk_size: int = DEFAULT_CHUNK_SIZE
    overlap: int = DEFAULT_OVERLAP

    async def execute(
        self,
        *,
        title: str,
        source_type: str,
        service: str | None,
        tags: Sequence[str],
        content: str,
    ) -> tuple[Document, int]:
        """Chunk + embed + persist one document. Returns the stored document and its chunk count."""
        chunks = await _chunk_and_embed(self.embedder, content, self.chunk_size, self.overlap)
        document = await self.documents.add(
            Document(title=title, source_type=source_type, service=service, tags=list(tags)),
            chunks,
        )
        await self.uow.commit()
        return document, len(chunks)


@dataclass
class UpdateDocument:
    """Replace an existing document's metadata and content — re-chunks and re-embeds the new
    content, discarding the old chunks entirely (no partial re-indexing)."""

    documents: DocumentRepository
    embedder: Embedder
    uow: UnitOfWork
    chunk_size: int = DEFAULT_CHUNK_SIZE
    overlap: int = DEFAULT_OVERLAP

    async def execute(
        self,
        document_id: uuid.UUID,
        *,
        title: str,
        source_type: str,
        service: str | None,
        tags: Sequence[str],
        content: str,
    ) -> tuple[Document, list[str]] | None:
        """Returns the updated document and its new chunks' text (in order), or `None` if
        `document_id` doesn't exist."""
        chunks = await _chunk_and_embed(self.embedder, content, self.chunk_size, self.overlap)
        document = await self.documents.update(
            document_id,
            Document(title=title, source_type=source_type, service=service, tags=list(tags)),
            chunks,
        )
        if document is None:
            return None
        await self.uow.commit()
        return document, [c.content for c in chunks]
