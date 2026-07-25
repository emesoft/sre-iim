"""IIM FastAPI application entrypoint.

Composes the interface-layer HTTP routers over the application/domain core (clean-architecture
layering, docs/ARCHITECTURE.md). Current scope: DB-aware /healthz, incident ingest/analysis (M2),
and knowledge-document ingest/list (M3 RAG slice). Stream/auth routers land in later milestones
(SPEC section 18).

FastAPI docs: https://fastapi.tiangolo.com/
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.infrastructure.config import get_settings
from app.interface.http.documents import router as documents_router
from app.interface.http.health import router as health_router
from app.interface.http.incidents import router as incidents_router
from app.interface.http.reports import router as reports_router

settings = get_settings()

app = FastAPI(
    title=f"{settings.app_name} API",
    description="Intelligent Incident Management - AI incident triage grounded in RAG.",
    version="0.1.0",
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


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {"app": settings.app_name, "docs": "/docs", "health": "/healthz"}
