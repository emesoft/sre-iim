"""Inbound request DTOs, one module per resource."""

from app.interface.http.dto.request.ado_connection import AdoConnectionCreateRequest
from app.interface.http.dto.request.auth import AdminLoginRequest
from app.interface.http.dto.request.cloud_connection import CloudConnectionCreateRequest
from app.interface.http.dto.request.document import DocumentIngestRequest
from app.interface.http.dto.request.incident import (
    ChatMessageRequest,
    IncidentIngestRequest,
    ResolveIncidentRequest,
)
from app.interface.http.dto.request.project import ProjectCreateRequest
from app.interface.http.dto.request.settings import SetTokenRequest

__all__ = [
    "IncidentIngestRequest",
    "ChatMessageRequest",
    "ResolveIncidentRequest",
    "DocumentIngestRequest",
    "CloudConnectionCreateRequest",
    "AdoConnectionCreateRequest",
    "ProjectCreateRequest",
    "SetTokenRequest",
    "AdminLoginRequest",
]
