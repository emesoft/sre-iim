"""Per-user login: email + password -> JWT access token. Replaces the old single shared-password
admin gate — see `require_role`/`get_current_user` in deps.py for the gate itself.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.application.auth.login import Login
from app.domain.users.entities import User
from app.domain.users.errors import InvalidCredentialsError
from app.interface.http.deps import get_current_user, get_login
from app.interface.http.dto import mappers
from app.interface.http.dto.request import LoginRequest
from app.interface.http.dto.response import LoginResponse, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, login_use_case: Login = Depends(get_login)) -> LoginResponse:
    try:
        user, token = await login_use_case.execute(body.email, body.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return LoginResponse(token=token, user=mappers.user_out(user))


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)) -> UserOut:
    return mappers.user_out(current_user)
