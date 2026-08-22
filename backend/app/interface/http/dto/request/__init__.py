"""Inbound request DTOs, one module per resource."""

from app.interface.http.dto.request.auth import AdminLoginRequest
from app.interface.http.dto.request.cloud_connection import CloudConnectionCreateRequest
from app.interface.http.dto.request.document import DocumentIngestRequest
from app.interface.http.dto.request.incident import (
    IncidentIngestRequest,
    LogSearchRequest,
    ResolveIncidentRequest,
)
from app.interface.http.dto.request.settings import SetTokenRequest

__all__ = [
    "IncidentIngestRequest",
    "LogSearchRequest",
    "ResolveIncidentRequest",
    "DocumentIngestRequest",
    "CloudConnectionCreateRequest",
    "SetTokenRequest",
    "AdminLoginRequest",
]
