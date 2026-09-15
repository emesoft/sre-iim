"""FastAPI dependency wiring — the composition root that assembles use cases from adapters.

This is the only place the concrete infrastructure (SQLAlchemy repos, Bedrock analyzer, system
clock) is bound to the domain ports. Tests override `get_session` and `get_base_analyzer` to run against
a disposable DB without calling Bedrock.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
import inspect
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.auth.entra_login import EntraLogin
from app.application.auth.login import Login
from app.application.integrations.manage import ManageIntegrations
from app.application.integrations.poll_alarms import PollAlarmsJob
from app.application.integrations.sso_connect import SsoConnections
from app.application.documents.ingest import IngestDocument, UpdateDocument
from app.application.documents.seed import SeedDefaultDocuments
from app.application.incidents.chat import IncidentChat
from app.application.incidents.daily_report import DailyReport
from app.application.incidents.ingest import IngestIncident
from app.application.incidents.rag_analyzer import RagAnalyzer
from app.application.incidents.resolve import ResolveIncident
from app.application.projects.manage import ManageProjects
from app.application.groups.manage import ManageGroups
from app.application.users.manage import ManageUsers
from app.domain.integrations.entities import Integration
from app.domain.integrations.ports import IntegrationRepository
from app.domain.documents.ports import DocumentRepository, Embedder, Retriever
from app.domain.incidents.ports import (
    Analyzer,
    ChatRepository,
    IncidentRepository,
    LogFetcher,
    TicketClient,
)
from app.domain.llm import ChatModel, IncidentChatProvider
from app.domain.projects.ports import ProjectRepository
from app.domain.users.entities import User
from app.domain.users.ports import UserRepository
from app.domain.groups.ports import GroupRepository
from app.domain.users.scope import ProjectScope
from app.infrastructure.clock import SystemClock
from app.infrastructure.cloud.aws_enricher import AwsContextEnricher
from app.infrastructure.config import Settings, get_settings
from app.infrastructure.db.repositories import (
    SqlAlchemyAnalysisCacheRepository,
    SqlAlchemyAppSettingsRepository,
    SqlAlchemyChatRepository,
    SqlAlchemyDailyReportRepository,
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
from app.infrastructure.llm import factory as llm_factory
from app.infrastructure.llm.anthropic_chat import AnthropicIncidentChat
from app.infrastructure.llm.catalog import ANTHROPIC, CLAUDE_CLI
from app.infrastructure.llm.claude_cli import ClaudeCliChat
from app.infrastructure.llm.factory import LlmProfile
from app.infrastructure.llm.jina_embedder import JinaEmbedder
from app.infrastructure.llm.titan_embedder import TitanEmbedder
from app.infrastructure.logs.factory import build_log_fetcher
from app.infrastructure.db.repositories.integrations import SqlAlchemyIntegrationRepository
from app.infrastructure.db.repositories.groups import SqlAlchemyGroupRepository
from app.infrastructure.integrations.registry import ProviderRegistry
from app.infrastructure.security.encryptor import Encryptor
from app.infrastructure.security.jwt import InvalidTokenError, decode_access_token

if TYPE_CHECKING:
    from fastapi import FastAPI


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async DB session for the request."""
    async with SessionLocal() as session:
        yield session


def _env_profile(settings: Settings) -> LlmProfile:
    return LlmProfile(id="environment", name="environment", provider=settings.llm_provider)


def select_base_analyzer(settings: Settings) -> Analyzer:
    """Pick the single-call analyzer from *environment* config alone. Pure — unit-testable, and
    still the answer for an instance whose Settings page has never been touched."""
    return llm_factory.build_analyzer(_env_profile(settings), settings)


async def get_base_analyzer(session: AsyncSession = Depends(get_session)) -> Analyzer:
    """The single-call analyzer for the *default* profile. Still a dependency of its own because
    tests override exactly this to avoid a real call; per-project resolution goes through
    `ConfiguredAnalyzers` below."""
    settings = get_settings()
    profile = await llm_factory.resolve(session, settings, get_encryptor())
    return llm_factory.build_analyzer(profile, settings)


def select_chat_model(settings: Settings) -> ChatModel:
    """Pick the ChatModel adapter (graph node LLM) from environment config. Pure — unit-testable."""
    return llm_factory.build_chat_model(_env_profile(settings), settings)


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


def get_log_fetcher_factory() -> Callable[[str], LogFetcher]:
    """Resolves a `LogFetcher` for a given incident's `service` (project -> cloud/account),
    per `PROJECT_<SERVICE>_*` env config (`infrastructure/config.py`). Tests override this to
    avoid a real AWS call."""
    settings = get_settings()
    return lambda service: build_log_fetcher(service, settings)


def get_chat_repository(session: AsyncSession = Depends(get_session)) -> ChatRepository:
    return SqlAlchemyChatRepository(session)


async def get_chat_provider(
    session: AsyncSession = Depends(get_session),
) -> IncidentChatProvider:
    """The chat backend for the active model profile.

    Anthropic profiles talk to the Messages API directly, which is what chat should have used all
    along: a bare `claude -p` call costs ~29k tokens of the CLI's own coding-agent harness before
    anything of ours is added, and a tool-using turn re-pays it on every internal step. The CLI
    path remains for the subscription profile, where there is no per-token bill and no alternative.
    """
    settings = get_settings()
    profile = await llm_factory.resolve(session, settings, get_encryptor())
    if profile.provider == ANTHROPIC:
        return AnthropicIncidentChat(
            api_key=profile.secrets.get("api_key", ""),
            model=profile.value("model"),
            settings=settings,
        )
    return ClaudeCliChat(settings)


#: Providers with a tool-calling chat adapter. Chat without tools is the "here are some commands
#: to run yourself" answer this feature exists to avoid, so an unsupported profile 501s rather than
#: degrading quietly into it.
CHAT_CAPABLE_PROVIDERS = (ANTHROPIC, CLAUDE_CLI)


async def chat_is_supported(session: AsyncSession = Depends(get_session)) -> bool:
    profile = await llm_factory.resolve(session, get_settings(), get_encryptor())
    return profile.provider in CHAT_CAPABLE_PROVIDERS


def get_incident_chat(
    session: AsyncSession = Depends(get_session),
    incidents: IncidentRepository = Depends(get_incident_repository),
    chat: ChatRepository = Depends(get_chat_repository),
    provider: IncidentChatProvider = Depends(get_chat_provider),
) -> IncidentChat:
    return IncidentChat(
        incidents=incidents,
        chat=chat,
        claude_chat=provider,
        uow=SqlAlchemyUnitOfWork(session),
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


def get_daily_report(
    session: AsyncSession = Depends(get_session),
    uow: SqlAlchemyUnitOfWork = Depends(get_unit_of_work),
) -> DailyReport:
    """GET /api/reports/daily flow: reuses the graph nodes' generic ChatModel for the digest
    narration, selected the same way as the graph analyzer (decision 0016)."""
    settings = get_settings()
    return DailyReport(
        incidents=SqlAlchemyIncidentRepository(session),
        chat=select_chat_model(settings),
        reports=SqlAlchemyDailyReportRepository(session),
        uow=uow,
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


async def call_provider(provider, session):
    """Call a dependency callable outside FastAPI, giving it a session only if it wants one.

    Background analysis runs with no request, so it invokes these factories by hand. The real
    `get_base_analyzer` takes a session (it reads the configured model profile from the database);
    a test override is typically a no-arg lambda returning a fake. Calling the real one with no
    arguments doesn't fail here — it silently passes the `Depends(...)` sentinel as the session and
    blows up much later inside a repository, which is exactly how this shipped broken once.
    """
    result = (
        provider(session)
        if "session" in inspect.signature(provider).parameters
        else provider()
    )
    return await result if inspect.isawaitable(result) else result


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
        base = await call_provider(base_provider, session)
        # No requester: background work bills to the project's profile or the default.
        analyzers = get_analyzers(
            session=session, base=base, embedder=embedder_provider(), current_user=None
        )
        ingest = IngestIncident(
            incidents=SqlAlchemyIncidentRepository(session),
            cache=SqlAlchemyAnalysisCacheRepository(session),
            analyzers=analyzers,
            clock=SystemClock(),
            uow=SqlAlchemyUnitOfWork(session),
            cache_ttl_seconds=settings.cache_ttl_seconds,
            enricher=get_context_enricher(session),
        )
        yield BackgroundIncidentDeps(
            ingest=ingest, documents=SqlAlchemyDocumentRepository(session)
        )


def get_user_repository(session: AsyncSession = Depends(get_session)) -> UserRepository:
    return SqlAlchemyUserRepository(session)


def get_group_repository(session: AsyncSession = Depends(get_session)) -> GroupRepository:
    return SqlAlchemyGroupRepository(session)


def get_login(
    users: UserRepository = Depends(get_user_repository),
    settings: Settings = Depends(get_settings),
) -> Login:
    return Login(
        users=users,
        jwt_secret=settings.jwt_secret_key,
        jwt_ttl_seconds=settings.jwt_access_token_ttl_seconds,
    )


def get_entra_login(
    users: UserRepository = Depends(get_user_repository),
    uow: SqlAlchemyUnitOfWork = Depends(get_unit_of_work),
    settings: Settings = Depends(get_settings),
) -> EntraLogin:
    return EntraLogin(
        users=users,
        uow=uow,
        jwt_secret=settings.jwt_secret_key,
        jwt_ttl_seconds=settings.jwt_access_token_ttl_seconds,
        tenant_id=settings.entra_tenant_id,
        client_id=settings.entra_client_id,
    )


def get_manage_groups(
    groups: GroupRepository = Depends(get_group_repository),
    users: UserRepository = Depends(get_user_repository),
    uow: SqlAlchemyUnitOfWork = Depends(get_unit_of_work),
) -> ManageGroups:
    return ManageGroups(groups=groups, users=users, uow=uow)


def get_manage_users(
    users: UserRepository = Depends(get_user_repository),
    uow: SqlAlchemyUnitOfWork = Depends(get_unit_of_work),
) -> ManageUsers:
    return ManageUsers(users=users, uow=uow)


async def get_current_user(
    authorization: str | None = Header(default=None),
    token_param: str | None = Query(default=None, alias="token"),
    settings: Settings = Depends(get_settings),
    users: UserRepository = Depends(get_user_repository),
) -> User:
    """Per-user auth gate, replacing the old shared-password `require_admin`. Parses
    `Authorization: Bearer <token>`, decodes/verifies it, and loads the user it names. 401 on any
    missing/invalid/expired token or a token naming a user that no longer exists — never a 500,
    so a bad/forged/stale token always reads as "please log in again".

    Falls back to a `?token=` query param when there's no Authorization header: the browser's
    native `EventSource` (used for the incident analysis SSE stream) cannot send custom headers,
    so that's the only way it can carry a JWT at all."""
    if not settings.jwt_secret_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="auth is misconfigured: JWT_SECRET_KEY is not set",
        )
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ")
    elif token_param:
        token = token_param
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


async def get_current_user_or_none(
    authorization: str | None = Header(default=None),
    token_param: str | None = Query(default=None, alias="token"),
    settings: Settings = Depends(get_settings),
    users: UserRepository = Depends(get_user_repository),
) -> User | None:
    """`get_current_user` without the 401.

    Used only where the identity is a nicety rather than a gate — currently to find the requesting
    group's model profile. The routes that build an analyzer already enforce auth through
    `require_role`, so this can never be the only thing standing between a stranger and the data.
    """
    try:
        return await get_current_user(authorization, token_param, settings, users)
    except HTTPException:
        return None


# Defined after `get_current_user_or_none` rather than next to the other analyzer factories:
# `Depends(...)` is evaluated at def time, so a dependency referenced here must already exist.
@dataclass
class ConfiguredAnalyzers:
    """Resolves the analysis engine for one project, per `ANALYSIS_MODE` and the project's model
    profile.

    `default_base` is the already-resolved analyzer for the default profile, injected as its own
    dependency so tests can replace it with a fake. A project that hasn't been pointed at a
    different profile uses it verbatim — which is what keeps "no per-project configuration" the
    cheap, obvious path, and what keeps every existing test override working.
    """

    session: AsyncSession
    embedder: Embedder
    default_base: Analyzer
    settings: Settings
    encryptor: Encryptor
    #: The requesting group's own model profile, if it has one. None for background work, which
    #: has no requester and therefore bills to the project's profile or the default.
    group_profile_id: str | None = None

    async def for_project(self, project: str | None) -> Analyzer:
        setup = await llm_factory.load_setup(self.session, self.encryptor)
        chosen = setup.for_project(project, self.group_profile_id)
        default = setup.for_project(None)
        retriever = SqlAlchemyRetriever(self.session)

        if self.settings.analysis_mode == "graph":
            profile = await llm_factory.resolve(
                self.session, self.settings, self.encryptor, project, self.group_profile_id
            )
            return _attributed(
                GraphAnalyzer(
                    llm_factory.build_chat_model(profile, self.settings),
                    self.embedder,
                    retriever,
                    model_label=f"graph:{llm_factory.model_label(profile, self.settings)}",
                    max_rounds=self.settings.max_rounds,
                    min_similarity=RETRIEVAL_MIN_SIMILARITY,
                ),
                profile,
            )

        base = self.default_base
        profile = chosen
        if chosen is not None and (default is None or chosen.id != default.id):
            profile = await llm_factory.resolve(
                self.session, self.settings, self.encryptor, project, self.group_profile_id
            )
            base = llm_factory.build_analyzer(profile, self.settings)
        return _attributed(
            RagAnalyzer(
                base=base,
                embedder=self.embedder,
                retriever=retriever,
                min_similarity=RETRIEVAL_MIN_SIMILARITY,
            ),
            profile,
        )


def _attributed(analyzer: Analyzer, profile) -> Analyzer:
    """Wraps an engine so its drafts carry the profile that paid for them — skipped when nothing
    is configured, since "the environment" isn't a profile anyone is billed for."""
    if profile is None or profile.id == "environment":
        return analyzer
    return llm_factory.AttributedAnalyzer(analyzer, profile.name)


def get_analyzers(
    session: AsyncSession = Depends(get_session),
    base: Analyzer = Depends(get_base_analyzer),
    embedder: Embedder = Depends(get_embedder),
    current_user: User | None = Depends(get_current_user_or_none),
) -> ConfiguredAnalyzers:
    """The analysis engine per project (decision 0011 for the mode; model profiles for the
    provider). Both engines implement the Analyzer port and self-retrieve, so the ingest use case
    is mode-agnostic (Open/Closed)."""
    return ConfiguredAnalyzers(
        session=session,
        embedder=embedder,
        default_base=base,
        settings=get_settings(),
        encryptor=get_encryptor(),
        group_profile_id=current_user.group_model_profile_id if current_user else None,
    )


def get_context_enricher(
    session: AsyncSession = Depends(get_session),
) -> AwsContextEnricher:
    """Live-evidence gathering for the project's own AWS account. Tests override this (or leave
    `IngestIncident.enricher` unset) so no analysis path ever depends on a real AWS call."""
    return AwsContextEnricher(
        integrations=SqlAlchemyIntegrationRepository(session), encryptor=get_encryptor()
    )


def get_ingest_incident(
    session: AsyncSession = Depends(get_session),
    analyzers: ConfiguredAnalyzers = Depends(get_analyzers),
    enricher: AwsContextEnricher = Depends(get_context_enricher),
) -> IngestIncident:
    """POST /api/incidents flow: cache-first analysis via the selected engine (single-pass RAG or
    the multi-agent graph)."""
    settings = get_settings()
    return IngestIncident(
        incidents=SqlAlchemyIncidentRepository(session),
        cache=SqlAlchemyAnalysisCacheRepository(session),
        analyzers=analyzers,
        clock=SystemClock(),
        uow=SqlAlchemyUnitOfWork(session),
        cache_ttl_seconds=settings.cache_ttl_seconds,
        enricher=enricher,
    )


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


async def get_project_scope(current_user: User = Depends(get_current_user)) -> ProjectScope:
    """Which projects this request may touch (see domain/users/scope.py).

    Resolved once here and passed into the queries, rather than each endpoint re-deriving it —
    and, crucially, enforced in the data layer rather than by hiding things in the UI: a hidden
    button is not access control when the endpoint behind it still answers.
    """
    if current_user.role == "admin":
        return ProjectScope.all()
    # The group's projects, which arrived with the user on the auth query — no second round trip,
    # and no chance of answering from a different snapshot than the one that set the role.
    return ProjectScope.of(current_user.projects)


def get_encryptor() -> Encryptor:
    return Encryptor(get_settings().secret_encryption_key)




def get_ado_ticket_client_factory(
    encryptor: Encryptor = Depends(get_encryptor),
) -> Callable[[Integration], TicketClient]:
    """Builds a `TicketClient` scoped to one resolved ticketing integration — a factory (not a
    plain `TicketClient` dependency) because which org/project/PAT to use isn't known until the
    incident's project has been looked up (`POST /api/incidents/{id}/ticket`, see incidents.py).
    Tests override this to avoid a real ADO call."""

    def _factory(integration: Integration) -> TicketClient:
        # Through the registry, so the Settings "Test" button and this route build the client the
        # same way — a divergence here would mean testing one destination and filing into another.
        return ProviderRegistry(encryptor=encryptor).ticket_sink(integration.provider).client_for(
            integration
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



def get_integration_repository(
    session: AsyncSession = Depends(get_session),
) -> IntegrationRepository:
    return SqlAlchemyIntegrationRepository(session)


#: One store for the whole process: a sign-in begun by one request is polled by the next, and the
#: browser only ever holds an opaque handle to it.
_SSO_CONNECTIONS = SsoConnections()


def get_sso_connections() -> SsoConnections:
    return _SSO_CONNECTIONS


def get_provider_registry(
    encryptor: Encryptor = Depends(get_encryptor),
) -> ProviderRegistry:
    """Which provider can do what, and how to build its adapters. Tests override this to avoid a
    real AWS/New Relic call (same convention as get_ticket_client)."""
    return ProviderRegistry(encryptor=encryptor)



def get_manage_integrations(
    integrations: IntegrationRepository = Depends(get_integration_repository),
    registry: ProviderRegistry = Depends(get_provider_registry),
    encryptor: Encryptor = Depends(get_encryptor),
    uow: SqlAlchemyUnitOfWork = Depends(get_unit_of_work),
) -> ManageIntegrations:
    return ManageIntegrations(
        integrations=integrations, registry=registry, encryptor=encryptor, uow=uow
    )


def get_poll_alarms_job(
    session: AsyncSession = Depends(get_session),
    registry: ProviderRegistry = Depends(get_provider_registry),
    analyzers: ConfiguredAnalyzers = Depends(get_analyzers),
    embedder: Embedder = Depends(get_embedder),
) -> PollAlarmsJob:
    """Manual-trigger path (`POST /api/cloud-connections/poll`). The scheduled path builds its own
    job with an independent session — see `_run_scheduled_poll` in main.py.

    `analyzers`/`embedder` are declared as `Depends(...)` (not called directly) so
    `app.dependency_overrides` actually reaches them in tests, same as every other use-case
    factory in this file. `ingest` still needs an `Analyzer` even though `PollAlarmsJob` itself
    never calls `analyze_incident` — alarm-created incidents wait for a manual "Analyze with AI"
    trigger (`POST /api/incidents/{id}/analyze`, see incidents.py), which builds its own
    `IngestIncident` via `get_ingest_incident`."""
    settings = get_settings()
    return PollAlarmsJob(
        integrations=SqlAlchemyIntegrationRepository(session),
        tracked=SqlAlchemyTrackedAlarmRepository(session),
        registry=registry,
        ingest=IngestIncident(
            incidents=SqlAlchemyIncidentRepository(session),
            cache=SqlAlchemyAnalysisCacheRepository(session),
            analyzers=analyzers,
            clock=SystemClock(),
            uow=SqlAlchemyUnitOfWork(session),
            cache_ttl_seconds=settings.cache_ttl_seconds,
            enricher=get_context_enricher(session),
        ),
        resolve=ResolveIncident(
            incidents=SqlAlchemyIncidentRepository(session),
            documents=SqlAlchemyDocumentRepository(session),
            embedder=embedder,
            uow=SqlAlchemyUnitOfWork(session),
        ),
        uow=SqlAlchemyUnitOfWork(session),
    )
