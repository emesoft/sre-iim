"""IIM FastAPI application entrypoint.

Composes the interface-layer HTTP routers over the application/domain core (clean-architecture
layering, docs/ARCHITECTURE.md). A background APScheduler job polls CloudWatch alarms hourly
(design spec 2026-08-22-cloudwatch-alarm-polling-design.md) using its own DB session, independent
of any request.

FastAPI docs: https://fastapi.tiangolo.com/
APScheduler docs: https://apscheduler.readthedocs.io/
"""

from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.application.cloud_connections.poll_alarms import PollAlarmsJob
from app.application.incidents.ingest import IngestIncident
from app.application.incidents.resolve import ResolveIncident
from app.infrastructure.clock import SystemClock
from app.infrastructure.config import get_settings
from app.infrastructure.db.repositories import (
    SqlAlchemyAnalysisCacheRepository,
    SqlAlchemyCloudConnectionRepository,
    SqlAlchemyDocumentRepository,
    SqlAlchemyIncidentRepository,
    SqlAlchemyTrackedAlarmRepository,
    SqlAlchemyUnitOfWork,
)
from app.infrastructure.db.session import SessionLocal
from app.infrastructure.cloud.cloudwatch_alarms import CloudWatchAlarmFetcher
from app.infrastructure.cloud.credential_resolver import CredentialResolver
from app.infrastructure.security.encryptor import Encryptor
from app.interface.http.cloud_connections import router as cloud_connections_router
from app.interface.http.deps import get_analyzer, get_base_analyzer, get_embedder
from app.interface.http.documents import router as documents_router
from app.interface.http.health import router as health_router
from app.interface.http.incidents import router as incidents_router
from app.interface.http.reports import router as reports_router

settings = get_settings()


async def _run_scheduled_poll() -> None:
    """Builds a PollAlarmsJob with its own session and runs one poll cycle. Wired into
    AsyncIOScheduler below; the manual `/api/cloud-connections/poll` endpoint uses the same
    PollAlarmsJob class via a request-scoped session in deps.get_poll_alarms_job."""
    async with SessionLocal() as session:
        embedder = get_embedder()
        job = PollAlarmsJob(
            connections=SqlAlchemyCloudConnectionRepository(session),
            tracked=SqlAlchemyTrackedAlarmRepository(session),
            fetcher=CloudWatchAlarmFetcher(
                CredentialResolver(Encryptor(settings.secret_encryption_key))
            ),
            ingest=IngestIncident(
                incidents=SqlAlchemyIncidentRepository(session),
                cache=SqlAlchemyAnalysisCacheRepository(session),
                analyzer=get_analyzer(session=session, base=get_base_analyzer(), embedder=embedder),
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
        await job.run()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_scheduled_poll, "interval", minutes=settings.alarm_poll_interval_minutes,
        id="poll_cloudwatch_alarms",
    )
    scheduler.start()
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
app.include_router(cloud_connections_router)


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {"app": settings.app_name, "docs": "/docs", "health": "/healthz"}
