"""SQLAlchemy document repository + pgvector retriever (implements the RAG ports).

`SqlAlchemyRetriever` runs the cosine-similarity search from docs/product/rag.md 7.3 using pgvector's
`<=>` (`cosine_distance`), pre-filtered by service/source_type and joined to the document title.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.documents.entities import Document, EmbeddedChunk, EvidenceRef, RetrievedChunk
from app.infrastructure.db.orm import DocChunkRow, DocumentRow
from app.infrastructure.db.repositories.mappers import document_to_domain


class SqlAlchemyDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, document: Document, chunks: list[EmbeddedChunk]) -> Document:
        row = DocumentRow(
            title=document.title,
            source_type=document.source_type,
            service=document.service,
            tags=list(document.tags),
            incident_id=document.incident_id,
        )
        self._s.add(row)
        await self._s.flush()
        await self._s.refresh(row)
        for chunk in chunks:
            self._s.add(
                DocChunkRow(
                    document_id=row.id,
                    source_type=row.source_type,
                    service=row.service,
                    chunk_index=chunk.index,
                    content=chunk.content,
                    embedding=chunk.embedding,
                )
            )
        await self._s.flush()
        return document_to_domain(row)

    async def list(self) -> list[tuple[Document, int]]:
        stmt = (
            select(DocumentRow, func.count(DocChunkRow.id))
            .join(DocChunkRow, DocChunkRow.document_id == DocumentRow.id, isouter=True)
            .group_by(DocumentRow.id)
            .order_by(DocumentRow.created_at.desc())
        )
        rows = (await self._s.execute(stmt)).all()
        return [(document_to_domain(doc), count) for doc, count in rows]

    async def get_with_content(self, document_id: uuid.UUID) -> tuple[Document, list[str]] | None:
        row = await self._s.get(DocumentRow, document_id)
        if row is None:
            return None
        chunks = (
            await self._s.execute(
                select(DocChunkRow.content)
                .where(DocChunkRow.document_id == document_id)
                .order_by(DocChunkRow.chunk_index)
            )
        ).scalars().all()
        return document_to_domain(row), list(chunks)

    async def evidence_refs(self, chunk_ids: list[uuid.UUID]) -> list[EvidenceRef]:
        """Resolve chunk ids to their (source_type, document title) for an analysis response."""
        if not chunk_ids:
            return []
        stmt = (
            select(DocChunkRow.id, DocChunkRow.source_type, DocumentRow.title)
            .join(DocumentRow, DocumentRow.id == DocChunkRow.document_id)
            .where(DocChunkRow.id.in_(chunk_ids))
        )
        rows = (await self._s.execute(stmt)).all()
        by_id = {cid: (st, title) for cid, st, title in rows}
        # Preserve the analysis's chunk order.
        refs: list[EvidenceRef] = []
        for cid in chunk_ids:
            if cid in by_id:
                st, title = by_id[cid]
                refs.append(EvidenceRef(chunk_id=cid, source_type=st, title=title))
        return refs


class SqlAlchemyRetriever:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def search(
        self,
        *,
        query_embedding: list[float],
        service: str | None,
        source_type: str | None = None,
        top_k: int = 6,
        min_similarity: float = 0.0,
    ) -> list[RetrievedChunk]:
        distance = DocChunkRow.embedding.cosine_distance(query_embedding)
        stmt = select(
            DocChunkRow, DocumentRow.title, DocumentRow.incident_id, distance.label("distance")
        ).join(DocumentRow, DocumentRow.id == DocChunkRow.document_id)
        if service is not None:
            stmt = stmt.where(or_(DocChunkRow.service == service, DocChunkRow.service.is_(None)))
        if source_type is not None:
            stmt = stmt.where(DocChunkRow.source_type == source_type)
        stmt = stmt.order_by(distance).limit(top_k)

        rows = (await self._s.execute(stmt)).all()
        results: list[RetrievedChunk] = []
        for row, title, incident_id, dist in rows:
            similarity = 1.0 - float(dist)
            if similarity >= min_similarity:
                results.append(
                    RetrievedChunk(
                        id=row.id,
                        document_id=row.document_id,
                        source_type=row.source_type,
                        service=row.service,
                        title=title,
                        content=row.content,
                        similarity=similarity,
                        incident_id=incident_id,
                    )
                )
        return results
