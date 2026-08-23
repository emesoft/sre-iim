"""SeedDefaultDocuments: idempotently ingest the bundled starter runbooks (app/seed_docs/*.md).

Skips any title that's already indexed, so re-running never duplicates a document or re-embeds
one that hasn't changed — this is meant to be safe to call on every fresh environment (or
repeatedly by hand) rather than run automatically on every backend startup.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.application.documents.ingest import IngestDocument
from app.domain.documents.ports import DocumentRepository
from app.domain.documents.seed_loader import load_seed_documents


@dataclass
class SeedDefaultDocuments:
    documents: DocumentRepository
    ingest: IngestDocument

    async def execute(self) -> tuple[int, int]:
        """Returns (seeded, skipped)."""
        existing_titles = {doc.title for doc, _ in await self.documents.list()}
        seeded = 0
        skipped = 0
        for seed in load_seed_documents():
            if seed.title in existing_titles:
                skipped += 1
                continue
            await self.ingest.execute(
                title=seed.title,
                source_type=seed.source_type,
                service=seed.service,
                tags=seed.tags,
                content=seed.content,
            )
            seeded += 1
        return seeded, skipped
