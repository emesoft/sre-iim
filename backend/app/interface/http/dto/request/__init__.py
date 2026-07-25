"""Inbound request DTOs, one module per resource."""

from app.interface.http.dto.request.document import DocumentIngestRequest
from app.interface.http.dto.request.incident import (
    IncidentIngestRequest,
    LogSearchRequest,
    ResolveIncidentRequest,
)

__all__ = [
    "IncidentIngestRequest",
    "LogSearchRequest",
    "ResolveIncidentRequest",
    "DocumentIngestRequest",
]
