"""Unit tests for deps.get_current_user's auth-token resolution — no DB, no HTTP client.

Covers the query-param fallback added for the incident SSE stream: the browser's native
EventSource can't send an Authorization header, so `get_current_user` must also accept the token
as `?token=`.
"""

import uuid
from dataclasses import dataclass

import pytest
from fastapi import HTTPException

from app.domain.users.entities import User
from app.infrastructure.security.jwt import create_access_token
from app.interface.http.deps import get_current_user

pytestmark = pytest.mark.asyncio


@dataclass
class _FakeSettings:
    jwt_secret_key: str = "test-secret"


class _FakeUserRepo:
    def __init__(self, user: User):
        self._user = user

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return self._user if user_id == self._user.id else None


def _user() -> User:
    return User(id=uuid.uuid4(), username="alice", password_hash="x", role="sre")


async def test_authorization_header_still_works():
    user = _user()
    token = create_access_token(user, "test-secret", ttl_seconds=3600)
    result = await get_current_user(
        authorization=f"Bearer {token}", token_param=None,
        settings=_FakeSettings(), users=_FakeUserRepo(user),
    )
    assert result.id == user.id


async def test_query_param_token_works_when_no_header():
    user = _user()
    token = create_access_token(user, "test-secret", ttl_seconds=3600)
    result = await get_current_user(
        authorization=None, token_param=token,
        settings=_FakeSettings(), users=_FakeUserRepo(user),
    )
    assert result.id == user.id


async def test_header_takes_precedence_over_query_param():
    user = _user()
    header_token = create_access_token(user, "test-secret", ttl_seconds=3600)
    result = await get_current_user(
        authorization=f"Bearer {header_token}", token_param="garbage-should-be-ignored",
        settings=_FakeSettings(), users=_FakeUserRepo(user),
    )
    assert result.id == user.id


async def test_no_header_and_no_query_param_is_401():
    with pytest.raises(HTTPException) as exc:
        await get_current_user(
            authorization=None, token_param=None,
            settings=_FakeSettings(), users=_FakeUserRepo(_user()),
        )
    assert exc.value.status_code == 401


async def test_invalid_query_param_token_is_401():
    with pytest.raises(HTTPException) as exc:
        await get_current_user(
            authorization=None, token_param="not-a-real-token",
            settings=_FakeSettings(), users=_FakeUserRepo(_user()),
        )
    assert exc.value.status_code == 401
