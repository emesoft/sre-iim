"""Admin login: a single shared password gates the Settings page (cloud connections + the Claude
Code token), not a per-user account system. See `require_admin` in deps.py for the gate itself.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, status

from app.infrastructure.config import Settings, get_settings
from app.infrastructure.security.admin_auth import create_admin_token
from app.interface.http.dto.request import AdminLoginRequest
from app.interface.http.dto.response import AdminLoginResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/admin-login", response_model=AdminLoginResponse)
async def admin_login(
    body: AdminLoginRequest, settings: Settings = Depends(get_settings)
) -> AdminLoginResponse:
    if not settings.admin_password or not settings.admin_jwt_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="admin login is misconfigured: ADMIN_PASSWORD/ADMIN_JWT_SECRET are not set",
        )
    if not secrets.compare_digest(body.password, settings.admin_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="incorrect password")
    return AdminLoginResponse(token=create_admin_token(settings.admin_jwt_secret))
