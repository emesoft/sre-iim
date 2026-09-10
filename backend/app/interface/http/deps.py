"""FastAPI dependency wiring — the composition root that assembles use cases from adapters.

This is the only place the concrete infrastructure (SQLAlchemy repos, Bedrock analyzer, system
clock) is bound to the domain ports. Tests override `get_session` and `get_analyzer` to run against
a disposable DB without calling Bedrock.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ado_connections.manage import ManageAdoConnections
from app.application.auth.login import Login
from app.application.cloud_connections.manage import ManageCloudConnections
from app.application.cloud_connections.poll_alarms import PollAlarmsJob
from app.application.documents.ingest import IngestDocument, UpdateDocument
from app.application.documents.seed import SeedDefaultDocuments
from app.application.incidents.chat import IncidentChat
from app.application.incidents.daily_report import DailyReport
from app.application.incidents.ingest import IngestIncident
from app.application.incidents.rag_analyzer import RagAnalyzer
from app.application.incidents.resolve import ResolveIncident
from app.application.projects.manage import ManageProjects
from app.application.users.manage import ManageUsers
from app.domain.ado_connections.entities import AdoConnection
from app.domain.ado_connections.ports import AdoConnectionRepository
from app.domain.cloud_connections.ports import AlarmFetcher, CloudConnectionRepository
from app.domain.documents.ports import DocumentRepository, Embedder, Retriever
from app.domain.incidents.ports import (
    Analyzer,
    ChatRepository,
    IncidentRepository,
    LogFetcher,
    TicketClient,
)
from app.domain.llm import ChatModel
from app.domain.projects.ports import ProjectRepository
from app.domain.users.entities import User
from app.domain.users.ports import UserRepository
from app.infrastructure.clock import SystemClock
from app.infrastructure.cloud.cloudwatch_alarms import CloudWatchAlarmFetcher
from app.infrastructure.cloud.credential_resolver import CredentialResolver
from app.infrastructure.config import Settings, get_settings
from app.infrastructure.db.repositories import (
    SqlAlchemyAdoConnectionRepository,
    SqlAlchemyAnalysisCacheRepository,
    SqlAlchemyAppSettingsRepository,
    SqlAlchemyChatRepository,
    SqlAlchemyCloudConnectionRepository,
    SqlAlchemyDocumentRepository,
    SqlAlchemyIncidentRepository,
    SqlAlchemyProjectRepository,
    SqlAlchemyRetriever,
    SqlAlchemyTrackedAlarmRepository,
    SqlAlchemyUnitOfWork,
    SqlAlchemyUserRepository,
)
from app.infrastructure.db.session import SessionLocal
from app.infrastructure.events import IncidentEventBus, default_bus
from app.infrastructure.graph.analyzer import GraphAnalyzer
from app.infrastructure.llm.bedrock_analyzer import BedrockAnalyzer
from app.infrastructure.llm.chat import BedrockChatModel, DeepSeekChatModel
from app.infrastructure.llm.claude_cli import ClaudeCliAnalyzer, ClaudeCliChat, ClaudeCliChatModel
from app.infrastructure.llm.deepseek_analyzer import DeepSeekAnalyzer
from app.infrastructure.llm.jina_embedder import JinaEmbedder
from app.infrastructure.llm.titan_embedder import TitanEmbedder
from app.infrastructure.logs.factory import build_log_fetcher
from app.infrastructure.security.encryptor import Encryptor
from app.infrastructure.security.jwt import InvalidTokenError, decode_access_token
from app.infrastructure.tickets.ado_client import AdoTicketClient

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
    if settings.llm_provider == "claude_cli":
        return ClaudeCliAnalyzer(settings)
    return BedrockAnalyzer(settings)


def get_base_analyzer() -> Analyzer:
    """Provider single-call analyzer (Bedrock/DeepSeek). Tests override this to avoid a real call."""
    return select_base_analyzer(get_settings())


def select_chat_model(settings: Settings) -> ChatModel:
    """Pick the ChatModel adapter (graph node LLM) from config. Pure — unit-testable."""
    if settings.llm_provider == "deepseek":
        return DeepSeekChatModel(settings)
    if settings.llm_provider == "claude_cli":
        return ClaudeCliChatModel(settings)
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


# Below this cosine similarity, a retrieved chunk is noise, not evidence — cuts token spend on
# irrelevant runbooks and stops the AI citing something unrelated as if it were grounded evidence.
# Picked empirically: a genuinely relevant runbook scored ~0.52 against a real incident's alert
# text, while unrelated ones in the same knowledge base scored ~0.18-0.39.
RETRIEVAL_MIN_SIMILARITY = 0.4


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
        if settings.llm_provider == "deepseek":
            main_model = settings.deepseek_model
        elif settings.llm_provider == "claude_cli":
            main_model = settings.claude_cli_model
        else:
            main_model = settings.model_id
        return GraphAnalyzer(
            select_chat_model(settings),
            embedder,
            retriever,
            model_label=f"graph:{main_model}",
            max_rounds=settings.max_rounds,
            min_similarity=RETRIEVAL_MIN_SIMILARITY,
        )
    return RagAnalyzer(
        base=base, embedder=embedder, retriever=retriever, min_similarity=RETRIEVAL_MIN_SIMILARITY
    )


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


def get_chat_repository(session: AsyncSession = Depends(get_session)) -> ChatRepository:
    return SqlAlchemyChatRepository(session)


def get_incident_chat(
    session: AsyncSession = Depends(get_session),
    incidents: IncidentRepository = Depends(get_incident_repository),
    chat: ChatRepository = Depends(get_chat_repository),
) -> IncidentChat:
    return IncidentChat(
        incidents=incidents,
        chat=chat,
        claude_chat=ClaudeCliChat(get_settings()),
        uow=SqlAlchemyUnitOfWork(session),
    )


def get_daily_report(
    session: AsyncSession = Depends(get_session),
) -> DailyReport:
    """GET /api/reports/daily flow: reuses the graph nodes' generic ChatModel for the digest
    narration, selected the same way as the graph analyzer (decision 0016)."""
    settings = get_settings()
    return DailyReport(
        incidents=SqlAlchemyIncidentRepository(session),
        chat=select_chat_model(settings),
    )


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


def get_unit_of_work(session: AsyncSession = Depends(get_session)) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(session)


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


def get_update_document(
    session: AsyncSession = Depends(get_session),
    embedder: Embedder = Depends(get_embedder),
) -> UpdateDocument:
    return UpdateDocument(
        documents=SqlAlchemyDocumentRepository(session),
        embedder=embedder,
        uow=SqlAlchemyUnitOfWork(session),
    )


def get_seed_default_documents(
    session: AsyncSession = Depends(get_session),
    ingest: IngestDocument = Depends(get_ingest_document),
) -> SeedDefaultDocuments:
    return SeedDefaultDocuments(documents=SqlAlchemyDocumentRepository(session), ingest=ingest)


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


def get_user_repository(session: AsyncSession = Depends(get_session)) -> UserRepository:
    return SqlAlchemyUserRepository(session)


def get_login(
    users: UserRepository = Depends(get_user_repository),
    settings: Settings = Depends(get_settings),
) -> Login:
    return Login(
        users=users,
        jwt_secret=settings.jwt_secret_key,
        jwt_ttl_seconds=settings.jwt_access_token_ttl_seconds,
    )


def get_manage_users(
    users: UserRepository = Depends(get_user_repository),
    uow: SqlAlchemyUnitOfWork = Depends(get_unit_of_work),
) -> ManageUsers:
    return ManageUsers(users=users, uow=uow)


async def get_current_user(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
    users: UserRepository = Depends(get_user_repository),
) -> User:
    """Per-user auth gate, replacing the old shared-password `require_admin`. Parses
    `Authorization: Bearer <token>`, decodes/verifies it, and loads the user it names. 401 on any
    missing/invalid/expired token or a token naming a user that no longer exists — never a 500,
    so a bad/forged/stale token always reads as "please log in again"."""
    if not settings.jwt_secret_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="auth is misconfigured: JWT_SECRET_KEY is not set",
        )
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")
    try:
        claims = decode_access_token(token, settings.jwt_secret_key)
    except InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required") from exc
    user = await users.get_by_id(uuid.UUID(claims["sub"]))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")
    return user


def require_role(*roles: str) -> Callable[..., Awaitable[User]]:
    """Dependency factory: 403 if the current user's role isn't one of `roles`. Used both at
    router level (e.g. `require_role("admin", "sre", "consultant")` — must be logged in) and on
    individual mutating routes (e.g. `require_role("admin", "sre")` — read-only for consultants)."""

    async def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"requires role in {list(roles)}",
            )
        return current_user

    return _check


def get_encryptor() -> Encryptor:
    return Encryptor(get_settings().secret_encryption_key)


def get_ado_connection_repository(
    session: AsyncSession = Depends(get_session),
) -> AdoConnectionRepository:
    return SqlAlchemyAdoConnectionRepository(session)


def get_manage_ado_connections(
    connections: AdoConnectionRepository = Depends(get_ado_connection_repository),
    encryptor: Encryptor = Depends(get_encryptor),
    uow: SqlAlchemyUnitOfWork = Depends(get_unit_of_work),
) -> ManageAdoConnections:
    return ManageAdoConnections(connections=connections, encryptor=encryptor, uow=uow)


def get_ado_ticket_client_factory(
    encryptor: Encryptor = Depends(get_encryptor),
) -> Callable[[AdoConnection], TicketClient]:
    """Builds a `TicketClient` scoped to one resolved `AdoConnection` — a factory (not a plain
    `TicketClient` dependency) because which org/project/PAT to use isn't known until the
    incident's project has been looked up (`POST /api/incidents/{id}/ticket`, see incidents.py).
    Tests override this to avoid a real ADO call."""

    def _factory(connection: AdoConnection) -> TicketClient:
        return AdoTicketClient(
            org=connection.org,
            project=connection.ado_project,
            pat=encryptor.decrypt(connection.encrypted_pat),
            work_item_type=connection.work_item_type,
        )

    return _factory


def get_project_repository(
    session: AsyncSession = Depends(get_session),
) -> ProjectRepository:
    return SqlAlchemyProjectRepository(session)


def get_manage_projects(
    projects: ProjectRepository = Depends(get_project_repository),
    uow: SqlAlchemyUnitOfWork = Depends(get_unit_of_work),
) -> ManageProjects:
    return ManageProjects(projects=projects, uow=uow)


def get_app_settings_repository(
    session: AsyncSession = Depends(get_session),
) -> SqlAlchemyAppSettingsRepository:
    return SqlAlchemyAppSettingsRepository(session)


def get_cloud_connection_repository(
    session: AsyncSession = Depends(get_session),
) -> CloudConnectionRepository:
    return SqlAlchemyCloudConnectionRepository(session)


def get_alarm_fetcher(
    encryptor: Encryptor = Depends(get_encryptor),
) -> AlarmFetcher:
    """Tests override this to avoid a real AWS call (same convention as get_ticket_client)."""
    return CloudWatchAlarmFetcher(CredentialResolver(encryptor))


def get_manage_cloud_connections(
    connections: CloudConnectionRepository = Depends(get_cloud_connection_repository),
    encryptor: Encryptor = Depends(get_encryptor),
    fetcher: AlarmFetcher = Depends(get_alarm_fetcher),
    uow: SqlAlchemyUnitOfWork = Depends(get_unit_of_work),
) -> ManageCloudConnections:
    return ManageCloudConnections(
        connections=connections, encryptor=encryptor, fetcher=fetcher, uow=uow
    )


def get_poll_alarms_job(
    session: AsyncSession = Depends(get_session),
    fetcher: AlarmFetcher = Depends(get_alarm_fetcher),
    analyzer: Analyzer = Depends(get_analyzer),
    embedder: Embedder = Depends(get_embedder),
) -> PollAlarmsJob:
    """Manual-trigger path (`POST /api/cloud-connections/poll`). The scheduled path builds its own
    job with an independent session — see `_run_scheduled_poll` in main.py.

    `analyzer`/`embedder` are declared as `Depends(...)` (not called directly) so
    `app.dependency_overrides` actually reaches them in tests, same as every other use-case
    factory in this file. `ingest` still needs an `Analyzer` even though `PollAlarmsJob` itself
    never calls `analyze_incident` — alarm-created incidents wait for a manual "Analyze with AI"
    trigger (`POST /api/incidents/{id}/analyze`, see incidents.py), which builds its own
    `IngestIncident` via `get_ingest_incident`."""
    settings = get_settings()
    return PollAlarmsJob(
        connections=SqlAlchemyCloudConnectionRepository(session),
        tracked=SqlAlchemyTrackedAlarmRepository(session),
        fetcher=fetcher,
        ingest=IngestIncident(
            incidents=SqlAlchemyIncidentRepository(session),
            cache=SqlAlchemyAnalysisCacheRepository(session),
            analyzer=analyzer,
            clock=SystemClock(),
            uow=SqlAlchemyUnitOfWork(session),
            cache_ttl_seconds=settings.cache_ttl_seconds,
        ),
        resolve=ResolveIncident(
            incidents=SqlAlchemyIncidentRepository(session),
            documents=SqlAlchemyDocumentRepository(session),
            embedder=embedder,
            uow=SqlAlchemyUnitOfWork(session),
        ),
        uow=SqlAlchemyUnitOfWork(session),
    )
