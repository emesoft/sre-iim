"""End-to-end HTTP tests for /api/settings/claude-token against real Postgres.

Overrides get_encryptor with a fixed test key so this suite passes regardless of the ambient
SECRET_ENCRYPTION_KEY env var (same pattern test_cloud_connections_http.py uses). Also overrides
get_current_user with a fake admin user — the role gate itself is covered by test_auth_http.py /
test_users_http.py; this suite tests the settings endpoints' own behavior, not the gate.
"""

import os
import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.users.entities import User
from app.infrastructure.db.orm import AnalysisRow, AppSettingRow, Base, IncidentRow
from app.infrastructure.security.encryptor import Encryptor
from app.interface.http.deps import get_current_user, get_encryptor, get_session
from app.main import app

_ADMIN_USER = User(
    id=uuid.uuid4(),
    username="admin",
    email="admin@test.local",
    password_hash="unused",
    role="admin",
    created_at=datetime.now(timezone.utc),
)

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://iim:iim@localhost:5432/iim"
)
_TEST_ENCRYPTION_KEY = "QMveDxMLB0eSF3PseIEr3fWyV7B0F5Ebk2KGC2JaZJk="


@pytest.fixture()
async def client():
    engine = create_async_engine(_DB_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"Postgres not reachable for HTTP test: {exc}")

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await s.execute(delete(AppSettingRow))
        await s.execute(delete(AnalysisRow))
        await s.execute(delete(IncidentRow))
        await s.commit()

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_encryptor] = lambda: Encryptor(_TEST_ENCRYPTION_KEY)
    app.dependency_overrides[get_current_user] = lambda: _ADMIN_USER

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.session_maker = maker  # type: ignore[attr-defined] - test-only escape hatch
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()


async def _seed_analysis(
    client, *, model_id: str, cache_state: str, input_tokens: int | None, output_tokens: int | None
) -> None:
    """Insert an incident + analysis directly — the llm-usage aggregate reads straight off the
    `analyses` table, so this exercises the query without re-running the full ingest pipeline."""
    async with client.session_maker() as s:
        incident_id = uuid.uuid4()
        s.add(
            IncidentRow(
                id=incident_id, service="GCM", source="manual", fingerprint=str(uuid.uuid4()),
                context={}, status="analyzed",
            )
        )
        await s.flush()
        s.add(
            AnalysisRow(
                incident_id=incident_id, severity="critical", summary="s", root_cause="r",
                recommended_action="a", cache_state=cache_state, model_id=model_id,
                input_tokens=input_tokens, output_tokens=output_tokens,
            )
        )
        await s.commit()


async def test_claude_token_is_not_set_by_default(client):
    r = await client.get("/api/settings/claude-token")
    assert r.status_code == 200
    assert r.json() == {"is_set": False}


async def test_setting_the_token_makes_is_set_true(client):
    r = await client.put("/api/settings/claude-token", json={"token": "sk-ant-oat-example"})
    assert r.status_code == 204, r.text

    r = await client.get("/api/settings/claude-token")
    assert r.status_code == 200
    assert r.json() == {"is_set": True}


async def test_the_response_never_includes_the_token_value(client):
    await client.put("/api/settings/claude-token", json={"token": "sk-ant-oat-example"})
    r = await client.get("/api/settings/claude-token")
    body = r.text
    assert "sk-ant-oat-example" not in body


async def test_setting_an_empty_token_is_422(client):
    r = await client.put("/api/settings/claude-token", json={"token": ""})
    assert r.status_code == 422


async def test_test_endpoint_reports_ok(client, monkeypatch):
    async def fake_verify(settings):
        return True, None

    monkeypatch.setattr(
        "app.interface.http.settings.verify_claude_cli_token", fake_verify
    )
    r = await client.post("/api/settings/claude-token/test")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "error": None}


async def test_test_endpoint_reports_the_failure_reason(client, monkeypatch):
    async def fake_verify(settings):
        return False, "claude CLI failed: 401 Invalid bearer token"

    monkeypatch.setattr(
        "app.interface.http.settings.verify_claude_cli_token", fake_verify
    )
    r = await client.post("/api/settings/claude-token/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "401" in body["error"]


async def test_llm_usage_is_empty_with_no_analyses(client):
    r = await client.get("/api/settings/llm-usage")
    assert r.status_code == 200
    assert r.json() == {"total_input_tokens": 0, "total_output_tokens": 0, "by_model": []}


async def test_llm_usage_sums_real_spend_grouped_by_model(client):
    await _seed_analysis(
        client, model_id="claude-cli:sonnet", cache_state="MISS", input_tokens=100, output_tokens=50
    )
    await _seed_analysis(
        client, model_id="claude-cli:sonnet", cache_state="MISS", input_tokens=200, output_tokens=80
    )
    # A cache HIT: no LLM call was made, must not add to the total.
    await _seed_analysis(
        client, model_id="claude-cli:sonnet", cache_state="HIT", input_tokens=0, output_tokens=0
    )
    # A provider that doesn't report usage: must be excluded, not counted as 0.
    await _seed_analysis(
        client, model_id="bedrock:haiku", cache_state="MISS", input_tokens=None, output_tokens=None
    )

    r = await client.get("/api/settings/llm-usage")
    assert r.status_code == 200
    body = r.json()
    assert body["total_input_tokens"] == 300
    assert body["total_output_tokens"] == 130
    assert body["by_model"] == [
        {
            "model_id": "claude-cli:sonnet",
            "input_tokens": 300,
            "output_tokens": 130,
            "analyses_count": 2,
        }
    ]
