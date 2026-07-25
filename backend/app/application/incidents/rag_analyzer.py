"""RagAnalyzer: a single-pass RAG analyzer that retrieves evidence then delegates to a base analyzer.

Implements the `Analyzer` port by composing an `Embedder`, a `Retriever`, and a base single-call
`Analyzer` (Bedrock/DeepSeek). It builds a retrieval query from the incident, fetches service-filtered
chunks, passes them to the base analyzer for grounded/cited reasoning, and reports the retrieved chunk
ids on the draft. This is the M3-slice-2 flow (US-011) expressed as an Analyzer, so the single ingest
use case stays analysis-mode-agnostic (Open/Closed: swap this for `GraphAnalyzer` with no use-case
change). Framework-free — depends only on domain ports and pure rules.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.domain.documents.entities import RetrievedChunk
from app.domain.documents.ports import Embedder, Retriever
from app.domain.incidents.entities import AnalysisDraft
from app.domain.incidents.ports import Analyzer, NullReporter, ProgressReporter
from app.domain.incidents.prompts import build_retrieval_query

# A retrieved chunk from a past resolved incident (source_type="incident") at or above this
# similarity is treated as "we've seen this before" — surfaced to the SRE as a known-issue match
# instead of a fresh diagnosis. Tuned conservatively; revisit once real matches accumulate.
KNOWN_ISSUE_SIMILARITY_THRESHOLD = 0.85


@dataclass
class RagAnalyzer:
    base: Analyzer
    embedder: Embedder
    retriever: Retriever
    top_k: int = 6
    min_similarity: float = 0.0

    async def analyze(
        self,
        context: dict,
        evidence: list[RetrievedChunk] | None = None,
        reporter: ProgressReporter | None = None,
    ) -> AnalysisDraft:
        reporter = reporter or NullReporter()
        chunks = await self._retrieve(context)
        await reporter.stage("retrieve", f"{len(chunks)} evidence chunk{'' if len(chunks) == 1 else 's'}")
        await reporter.stage("analyze")
        draft = await self.base.analyze(context, evidence=chunks)
        known = next(
            (
                c
                for c in chunks
                if c.source_type == "incident" and c.similarity >= KNOWN_ISSUE_SIMILARITY_THRESHOLD
            ),
            None,
        )
        return replace(
            draft,
            evidence_chunk_ids=tuple(chunk.id for chunk in chunks),
            known_issue_incident_id=known.incident_id if known else None,
            known_issue_similarity=known.similarity if known else None,
        )

    async def _retrieve(self, context: dict) -> list[RetrievedChunk]:
        query = build_retrieval_query(context)
        if not query:
            return []
        query_embedding = await self.embedder.embed_query(query)
        return await self.retriever.search(
            query_embedding=query_embedding,
            service=context.get("service"),
            top_k=self.top_k,
            min_similarity=self.min_similarity,
        )
