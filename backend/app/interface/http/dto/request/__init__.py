"""Inbound request DTOs, one module per resource."""

from app.interface.http.dto.request.document import DocumentIngestRequest
from app.interface.http.dto.request.incident import IncidentIngestRequest, LogSearchRequest

__all__ = ["IncidentIngestRequest", "LogSearchRequest", "DocumentIngestRequest"]
