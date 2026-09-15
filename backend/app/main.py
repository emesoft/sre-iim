"""IIM FastAPI application entrypoint.

Composes the interface-layer HTTP routers over the application/domain core (clean-architecture
layering, docs/ARCHITECTURE.md). A background APScheduler job polls CloudWatch alarms hourly
(design spec 2026-08-22-cloudwatch-alarm-polling-design.md) using its own DB session, independent
of any request.

FastAPI docs: https://fastapi.tiangolo.com/
APScheduler docs: https://apscheduler.readthedocs.io/
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.application.integrations.poll_alarms import PollAlarmsJob
from app.application.incidents.auto_analyze import AutoAnalyzeIncidents
from app.application.incidents.ingest import IngestIncident
from app.application.incidents.resolve import ResolveIncident
from app.application.users.manage import ManageUsers
from app.infrastructure.clock import SystemClock
from app.infrastructure.config import get_settings
from app.infrastructure.db.repositories import (
    SqlAlchemyAnalysisCacheRepository,
    SqlAlchemyDocumentRepository,
    SqlAlchemyIncidentRepository,
    SqlAlchemyTrackedAlarmRepository,
    SqlAlchemyUnitOfWork,
    SqlAlchemyUserRepository,
)
from app.infrastructure.db.session import SessionLocal
from app.infrastructure.db.repositories.integrations import SqlAlchemyIntegrationRepository
from app.infrastructure.integrations.registry import ProviderRegistry
from app.infrastructure.security.encryptor import Encryptor
from app.interface.http.auth import router as auth_router
from app.interface.http.deps import (
    get_analyzers,
    get_base_analyzer,
    get_context_enricher,
    get_embedder,
)
from app.interface.http.documents import router as documents_router
from app.interface.http.health import router as health_router
from app.interface.http.incidents import router as incidents_router
from app.interface.http.integrations import providers_router
from app.interface.http.integrations import router as integrations_router
from app.interface.http.projects import router as projects_router
from app.interface.http.reports import router as reports_router
from app.interface.http.settings import router as settings_router
from app.interface.http.groups import router as groups_router
from app.interface.http.users import router as users_router
from app.interface.http.webhooks import router as webhooks_router

settings = get_settings()


async def _requeue_interrupted(incidents, uow, settings) -> None:
    """Put back anything a restart left mid-analysis. Runs on every poll cycle, not only at boot,
    so a crash at 02:00 is recovered by 02:05 rather than by whoever notices in the morning."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.analysis_stale_minutes)
    requeued = await incidents.reset_stale_analyzing(cutoff)
    if requeued:
        await uow.commit()
        logger.warning("re-queued %d incident(s) left stuck in analyzing", requeued)


async def _run_scheduled_poll() -> None:
    """Builds a PollAlarmsJob with its own session and runs one poll cycle. Wired into
    AsyncIOScheduler below; the manual `/api/cloud-connections/poll` endpoint uses the same
    PollAlarmsJob class via a request-scoped session in deps.get_poll_alarms_job."""
    async with SessionLocal() as session:
        embedder = get_embedder()
        encryptor = Encryptor(settings.secret_encryption_key)
        # Resolved once per cycle: the scheduler has no request to hang a dependency off, and a
        # provider switched on the Settings page should take effect on the next poll.
        analyzers = get_analyzers(
            session=session,
            base=await get_base_analyzer(session),
            embedder=embedder,
            current_user=None,  # the scheduler has no requester
        )
        job = PollAlarmsJob(
            integrations=SqlAlchemyIntegrationRepository(session),
            tracked=SqlAlchemyTrackedAlarmRepository(session),
            registry=ProviderRegistry(encryptor=encryptor),
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
        await job.run()

        # Straight after the poll, while whatever it just opened is the freshest thing in the
        # table. Separate from the poll on purpose: it also picks up incidents created by the
        # manual Refresh button and by webhooks, which the poller never sees again.
        incidents = SqlAlchemyIncidentRepository(session)
        await _requeue_interrupted(incidents, SqlAlchemyUnitOfWork(session), settings)
        await AutoAnalyzeIncidents(
            incidents=incidents,
            ingest=IngestIncident(
                incidents=incidents,
                cache=SqlAlchemyAnalysisCacheRepository(session),
                analyzers=analyzers,
                clock=SystemClock(),
                uow=SqlAlchemyUnitOfWork(session),
                cache_ttl_seconds=settings.cache_ttl_seconds,
                enricher=get_context_enricher(session),
            ),
            priorities=_auto_analyze_priorities(),
            limit=settings.auto_analyze_max_per_run,
        ).run()


def _auto_analyze_priorities() -> tuple[str, ...]:
    return tuple(p.strip().lower() for p in settings.auto_analyze_priorities.split(",") if p.strip())


async def _seed_initial_admin() -> None:
    """If INITIAL_ADMIN_USERNAME/INITIAL_ADMIN_PASSWORD are set and no users exist yet, create the
    first admin account — otherwise a fresh database has no way to log in at all. Safe to call on
    every startup: it only acts when the `users` table is empty. INITIAL_ADMIN_EMAIL is optional,
    purely informational (login is by username)."""
    if not settings.initial_admin_username or not settings.initial_admin_password:
        return
    async with SessionLocal() as session:
        users = SqlAlchemyUserRepository(session)
        if await users.list_all():
            return
        manager = ManageUsers(users=users, uow=SqlAlchemyUnitOfWork(session))
        await manager.create(
            settings.initial_admin_username,
            settings.initial_admin_password,
            "admin",
            email=settings.initial_admin_email,
        )


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _seed_initial_admin()
    # A restart is precisely when an in-flight analysis gets orphaned, so recover before serving
    # rather than waiting for the first poll cycle.
    async with SessionLocal() as session:
        await _requeue_interrupted(
            SqlAlchemyIncidentRepository(session), SqlAlchemyUnitOfWork(session), settings
        )
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_scheduled_poll, "interval", minutes=settings.alarm_poll_interval_minutes,
        id="poll_cloudwatch_alarms",
    )
    scheduler.start()
    app.state.scheduler = scheduler
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(
    title=f"{settings.app_name} API",
    description="Intelligent Incident Management - AI incident triage grounded in RAG.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(incidents_router)
app.include_router(documents_router)
app.include_router(reports_router)
app.include_router(integrations_router)
app.include_router(providers_router)
app.include_router(projects_router)
app.include_router(settings_router)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(groups_router)
app.include_router(webhooks_router)


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {"app": settings.app_name, "docs": "/docs", "health": "/healthz"}
