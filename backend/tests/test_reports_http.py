"""HTTP test for GET /api/reports/daily — overrides the whole DailyReport dependency with fakes
(no DB, no LLM call needed to test the endpoint's request/response wiring). Also overrides
get_current_user with a fake consultant — reports.py is gated to "must be logged in as any role"
now that per-user auth exists, and a consultant (read-only) is the most permissive-adjacent role
to prove reads stay open to everyone.
"""

import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.application.incidents.daily_report import DailyReport
from app.domain.users.entities import User
from app.interface.http.deps import get_current_user, get_daily_report
from app.main import app
from tests.test_daily_report import FakeChatModel, FakeIncidentRepo, _analysis, _incident

pytestmark = pytest.mark.asyncio

_CONSULTANT_USER = User(
    id=uuid.uuid4(),
    email="consultant@test.local",
    password_hash="unused",
    role="consultant",
    created_at=datetime.now(timezone.utc),
)


async def test_get_daily_report_returns_counts_and_markdown():
    import datetime as dt

    i1 = _incident("GCM", "resolved", dt.datetime(2026, 7, 25, 10, tzinfo=dt.timezone.utc))
    a1 = _analysis(i1.id, "critical", "OOM")
    repo = FakeIncidentRepo([(i1, a1)])
    app.dependency_overrides[get_daily_report] = lambda: DailyReport(
        incidents=repo, chat=FakeChatModel()
    )
    app.dependency_overrides[get_current_user] = lambda: _CONSULTANT_USER

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/reports/daily", params={"date": "2026-07-25"})

    app.dependency_overrides.clear()

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["report_date"] == "2026-07-25"
    assert body["counts_by_severity"] == {"critical": 1}
    assert body["counts_by_status"] == {"resolved": 1}
    assert len(body["incidents"]) == 1
    assert body["incidents"][0]["service"] == "GCM"
    assert "2 incidents" in body["slack_markdown"] or "incidents" in body["slack_markdown"]


async def test_get_daily_report_filters_by_service():
    import datetime as dt

    gcm = _incident("GCM", "new", dt.datetime(2026, 7, 25, 10, tzinfo=dt.timezone.utc))
    evp = _incident("EVP", "new", dt.datetime(2026, 7, 25, 11, tzinfo=dt.timezone.utc))
    repo = FakeIncidentRepo([(gcm, None), (evp, None)])
    app.dependency_overrides[get_daily_report] = lambda: DailyReport(
        incidents=repo, chat=FakeChatModel()
    )
    app.dependency_overrides[get_current_user] = lambda: _CONSULTANT_USER

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/reports/daily", params={"date": "2026-07-25", "service": "GCM"})

    app.dependency_overrides.clear()

    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["incidents"]) == 1
    assert body["incidents"][0]["service"] == "GCM"
