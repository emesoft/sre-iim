"""FastAPI dependency wiring — the composition root that assembles use cases from adapters.

This is the only place the concrete infrastructure (SQLAlchemy repos, Bedrock analyzer, system
clock) is bound to the domain ports. Tests override `get_session` and `get_analyzer` to run against
a disposable DB without calling Bedrock.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.documents.ingest import IngestDocument
from app.application.incidents.ingest import IngestIncident
from app.application.incidents.rag_analyzer import RagAnalyzer
from app.application.incidents.resolve import ResolveIncident
from app.domain.documents.ports import DocumentRepository, Embedder, Retriever
from app.domain.incidents.ports import Analyzer, IncidentRepository, LogFetcher
from app.domain.llm import ChatModel
from app.infrastructure.clock import SystemClock
from app.infrastructure.config import Settings, get_settings
from app.infrastructure.db.repositories import (
    SqlAlchemyAnalysisCacheRepository,
    SqlAlchemyDocumentRepository,
    SqlAlchemyIncidentRepository,
    SqlAlchemyRetriever,
    SqlAlchemyUnitOfWork,
)
from app.infrastructure.db.session import SessionLocal
from app.infrastructure.events import IncidentEventBus, default_bus
from app.infrastructure.graph.analyzer import GraphAnalyzer
from app.infrastructure.llm.bedrock_analyzer import BedrockAnalyzer
from app.infrastructure.llm.chat import BedrockChatModel, DeepSeekChatModel
from app.infrastructure.llm.deepseek_analyzer import DeepSeekAnalyzer
from app.infrastructure.llm.jina_embedder import JinaEmbedder
from app.infrastructure.llm.titan_embedder import TitanEmbedder
from app.infrastructure.logs.factory import build_log_fetcher

if TYPE_CHECKING:
    from fastapi import FastAPI


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async DB session for the request."""
    async with SessionLocal() as session:
        yield session


def select_base_analyzer(settings: Settings) -> Analyzer:
    """Pick the single-call provider analyzer from config (decision 0016). Pure — unit-testable."""
    if settings.llm_provider == "deepseek":
        return DeepSeekAnalyzer(settings)
    return BedrockAnalyzer(settings)


def get_base_analyzer() -> Analyzer:
    """Provider single-call analyzer (Bedrock/DeepSeek). Tests override this to avoid a real call."""
    return select_base_analyzer(get_settings())


def select_chat_model(settings: Settings) -> ChatModel:
    """Pick the ChatModel adapter (graph node LLM) from config. Pure — unit-testable."""
    if settings.llm_provider == "deepseek":
        return DeepSeekChatModel(settings)
    return BedrockChatModel(settings)


def select_embedder(settings: Settings) -> Embedder:
    """Pick the Embedder adapter from config (decision 0016). Pure — unit-testable."""
    if settings.embedding_provider == "jina":
        return JinaEmbedder(settings)
    return TitanEmbedder(settings)


def get_embedder() -> Embedder:
    """The embedding backend, selected by EMBEDDING_PROVIDER. Tests override it to avoid a real call."""
    return select_embedder(get_settings())


def get_incident_repository(
    session: AsyncSession = Depends(get_session),
) -> IncidentRepository:
    return SqlAlchemyIncidentRepository(session)


def get_analyzer(
    session: AsyncSession = Depends(get_session),
    base: Analyzer = Depends(get_base_analyzer),
    embedder: Embedder = Depends(get_embedder),
) -> Analyzer:
    """The analysis engine, selected by ANALYSIS_MODE (decision 0011): `single` = single-pass RAG,
    `graph` = the multi-agent LangGraph graph. Both implement the Analyzer port and self-retrieve, so
    the ingest use case is mode-agnostic (Open/Closed)."""
    settings = get_settings()
    retriever = SqlAlchemyRetriever(session)
    if settings.analysis_mode == "graph":
        main_model = (
            settings.deepseek_model if settings.llm_provider == "deepseek" else settings.model_id
        )
        return GraphAnalyzer(
            select_chat_model(settings),
            embedder,
            retriever,
            model_label=f"graph:{main_model}",
            max_rounds=settings.max_rounds,
        )
    return RagAnalyzer(base=base, embedder=embedder, retriever=retriever)


def get_ingest_incident(
    session: AsyncSession = Depends(get_session),
    analyzer: Analyzer = Depends(get_analyzer),
) -> IngestIncident:
    """POST /api/incidents flow: cache-first analysis via the selected engine (single-pass RAG or
    the multi-agent graph)."""
    settings = get_settings()
    return IngestIncident(
        incidents=SqlAlchemyIncidentRepository(session),
        cache=SqlAlchemyAnalysisCacheRepository(session),
        analyzer=analyzer,
        clock=SystemClock(),
        uow=SqlAlchemyUnitOfWork(session),
        cache_ttl_seconds=settings.cache_ttl_seconds,
    )


def get_log_fetcher_factory() -> Callable[[str], LogFetcher]:
    """Resolves a `LogFetcher` for a given incident's `service` (project -> cloud/account),
    per `PROJECT_<SERVICE>_*` env config (`infrastructure/config.py`). Tests override this to
    avoid a real AWS call."""
    settings = get_settings()
    return lambda service: build_log_fetcher(service, settings)


def get_resolve_incident(
    session: AsyncSession = Depends(get_session),
    embedder: Embedder = Depends(get_embedder),
) -> ResolveIncident:
    """POST /api/incidents/{id}/resolve flow: mark resolved + save the case for known-issue
    matching, reusing the same embedder as document ingestion."""
    return ResolveIncident(
        incidents=SqlAlchemyIncidentRepository(session),
        documents=SqlAlchemyDocumentRepository(session),
        embedder=embedder,
        uow=SqlAlchemyUnitOfWork(session),
    )


def get_document_repository(
    session: AsyncSession = Depends(get_session),
) -> DocumentRepository:
    return SqlAlchemyDocumentRepository(session)


def get_retriever(session: AsyncSession = Depends(get_session)) -> Retriever:
    return SqlAlchemyRetriever(session)


def get_ingest_document(
    session: AsyncSession = Depends(get_session),
    embedder: Embedder = Depends(get_embedder),
) -> IngestDocument:
    return IngestDocument(
        documents=SqlAlchemyDocumentRepository(session),
        embedder=embedder,
        uow=SqlAlchemyUnitOfWork(session),
    )


def get_event_bus() -> IncidentEventBus:
    """The process-wide in-process pub/sub for live incident-analysis progress (SSE streaming
    design, decision 2026-07-20). A single shared instance — this is a local dev/test app running
    as one process, not a multi-worker deployment."""
    return default_bus


@dataclass
class BackgroundIncidentDeps:
    """Everything a background analysis task needs, bound to its own DB session."""

    ingest: IngestIncident
    documents: DocumentRepository


@asynccontextmanager
async def resolve_background_incident_deps(app: "FastAPI") -> AsyncIterator[BackgroundIncidentDeps]:
    """Build fresh incident-analysis dependencies for a background task scheduled after the
    request's own session has already closed. Honors `app.dependency_overrides` for `get_session`
    / `get_base_analyzer` / `get_embedder` (the ones tests actually override) so tests exercise the
    same fakes the request path used, while still opening an independent session."""
    session_dep = app.dependency_overrides.get(get_session, get_session)
    base_provider = app.dependency_overrides.get(get_base_analyzer, get_base_analyzer)
    embedder_provider = app.dependency_overrides.get(get_embedder, get_embedder)
    settings = get_settings()
    async with asynccontextmanager(session_dep)() as session:
        analyzer = get_analyzer(session=session, base=base_provider(), embedder=embedder_provider())
        ingest = IngestIncident(
            incidents=SqlAlchemyIncidentRepository(session),
            cache=SqlAlchemyAnalysisCacheRepository(session),
            analyzer=analyzer,
            clock=SystemClock(),
            uow=SqlAlchemyUnitOfWork(session),
            cache_ttl_seconds=settings.cache_ttl_seconds,
        )
        yield BackgroundIncidentDeps(
            ingest=ingest, documents=SqlAlchemyDocumentRepository(session)
        )
