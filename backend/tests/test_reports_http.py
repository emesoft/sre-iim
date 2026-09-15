"""HTTP tests for GET /api/reports/daily — the whole DailyReport dependency is faked (no DB, no
LLM call needed to test request/response wiring), as is the caller: a consultant, since reports
are readable by every role.

`get_project_scope` is overridden too. A digest is cached per (date, service), so access is
checked on the request rather than by filtering the result — see the route's docstring — and these
tests cover both sides of that rule.
"""

import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.domain.users.entities import User
from app.domain.users.scope import ProjectScope
from app.interface.http.deps import get_current_user, get_daily_report, get_project_scope
from app.main import app
from tests.test_daily_report import FakeChatModel, FakeIncidentRepo, _analysis, _daily_report, _incident

pytestmark = pytest.mark.asyncio

_CONSULTANT_USER = User(
    id=uuid.uuid4(),
    username="consultant",
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
    app.dependency_overrides[get_daily_report] = lambda: _daily_report(repo, FakeChatModel())
    app.dependency_overrides[get_current_user] = lambda: _CONSULTANT_USER
    # Unrestricted: the all-projects digest is admin-only, and this test is about the payload.
    app.dependency_overrides[get_project_scope] = ProjectScope.all

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
    app.dependency_overrides[get_daily_report] = lambda: _daily_report(repo, FakeChatModel())
    app.dependency_overrides[get_current_user] = lambda: _CONSULTANT_USER
    app.dependency_overrides[get_project_scope] = lambda: ProjectScope.of(["GCM"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/reports/daily", params={"date": "2026-07-25", "service": "GCM"})

    app.dependency_overrides.clear()

    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["incidents"]) == 1
    assert body["incidents"][0]["service"] == "GCM"


async def test_a_scoped_user_cannot_read_another_projects_digest():
    repo = FakeIncidentRepo([])
    app.dependency_overrides[get_daily_report] = lambda: _daily_report(repo, FakeChatModel())
    app.dependency_overrides[get_current_user] = lambda: _CONSULTANT_USER
    app.dependency_overrides[get_project_scope] = lambda: ProjectScope.of(["GCM"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/reports/daily", params={"date": "2026-07-25", "service": "EVP"})

    app.dependency_overrides.clear()
    assert r.status_code == 403
    assert "EVP" in r.json()["detail"]


async def test_a_scoped_user_cannot_read_the_all_projects_digest():
    """Not a filtered version of it either: the digest is cached per (date, service), so serving a
    scoped one under the all-projects key would hand it to admins as if it were complete."""
    repo = FakeIncidentRepo([])
    app.dependency_overrides[get_daily_report] = lambda: _daily_report(repo, FakeChatModel())
    app.dependency_overrides[get_current_user] = lambda: _CONSULTANT_USER
    app.dependency_overrides[get_project_scope] = lambda: ProjectScope.of(["GCM"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/reports/daily", params={"date": "2026-07-25"})

    app.dependency_overrides.clear()
    assert r.status_code == 403
