"""Inbound request DTOs, one module per resource."""

from app.interface.http.dto.request.integration import (
    IntegrationCreateRequest,
    SsoBeginRequest,
    SsoFinishRequest,
)
from app.interface.http.dto.request.auth import EntraLoginRequest, LoginRequest
from app.interface.http.dto.request.document import DocumentIngestRequest
from app.interface.http.dto.request.incident import (
    BulkIncidentRequest,
    ChatMessageRequest,
    IncidentIngestRequest,
    ResolveIncidentRequest,
)
from app.interface.http.dto.request.project import ProjectCreateRequest
from app.interface.http.dto.request.group import AssignGroupRequest, GroupWriteRequest
from app.interface.http.dto.request.settings import (
    SaveLlmProfileRequest,
    SetDefaultProfileRequest,
    SetProjectProfileRequest,
    SetTokenRequest,
)
from app.interface.http.dto.request.users import (
    CreateUserRequest,
    ResetPasswordRequest,

)

__all__ = [
    "IncidentIngestRequest",
    "BulkIncidentRequest",
    "ChatMessageRequest",
    "ResolveIncidentRequest",
    "DocumentIngestRequest",
    "ProjectCreateRequest",
    "AssignGroupRequest",
    "GroupWriteRequest",
    "SaveLlmProfileRequest",
    "SetDefaultProfileRequest",
    "SetProjectProfileRequest",
    "SetTokenRequest",
    "LoginRequest",
    "IntegrationCreateRequest",
    "SsoBeginRequest",
    "SsoFinishRequest",
    "EntraLoginRequest",
    "CreateUserRequest",
    "ResetPasswordRequest",
]
