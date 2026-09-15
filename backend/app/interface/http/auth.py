"""Per-user login: username + password -> JWT access token. Replaces the old single shared-password
admin gate — see `require_role`/`get_current_user` in deps.py for the gate itself.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.application.auth.entra_login import EntraLogin
from app.application.auth.login import Login
from app.domain.users.entities import User
from app.domain.users.errors import InvalidCredentialsError
from app.infrastructure.security.entra import EntraTokenError
from app.interface.http.deps import (
    get_current_user,
    get_entra_login,
    get_login,
)
from app.interface.http.dto import mappers
from app.interface.http.dto.request import EntraLoginRequest, LoginRequest
from app.interface.http.dto.response import EntraConfigOut, LoginResponse, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    login_use_case: Login = Depends(get_login),
) -> LoginResponse:
    try:
        user, token = await login_use_case.execute(body.username, body.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return LoginResponse(token=token, user=mappers.user_out(user))


@router.get("/entra/config", response_model=EntraConfigOut)
async def entra_config(entra: EntraLogin = Depends(get_entra_login)) -> EntraConfigOut:
    """What the browser needs to start a sign-in, and whether it can. Both ids are public — they
    travel to Microsoft in the authorize URL — so this route is deliberately unauthenticated: the
    login screen has to read it before anyone is logged in."""
    return EntraConfigOut(
        enabled=entra.configured, tenant_id=entra.tenant_id, client_id=entra.client_id
    )


@router.post("/entra", response_model=LoginResponse)
async def entra_login(
    body: EntraLoginRequest,
    entra: EntraLogin = Depends(get_entra_login),
) -> LoginResponse:
    """Exchange a verified Entra ID token for this application's own access token."""
    if not entra.configured:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Entra sign-in is not configured (set ENTRA_TENANT_ID and ENTRA_CLIENT_ID)",
        )
    try:
        user, token = await entra.execute(body.id_token)
    except EntraTokenError as exc:
        # The detail stays generic: why a token failed verification is a hint worth withholding.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Entra sign-in failed"
        ) from exc
    return LoginResponse(token=token, user=mappers.user_out(user))


@router.get("/me", response_model=UserOut)
async def me(
    current_user: User = Depends(get_current_user),
) -> UserOut:
    """Includes the caller's project memberships: the app decides between "show the console" and
    "you're not in any project yet" from this, so it has to be the same list the API enforces."""
    return mappers.user_out(current_user)
