"""HTTP test for GET /api/reports/daily — overrides the whole DailyReport dependency with fakes
(no DB, no LLM call needed to test the endpoint's request/response wiring).
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.application.incidents.daily_report import DailyReport
from app.interface.http.deps import get_daily_report
from app.main import app
from tests.test_daily_report import FakeChatModel, FakeIncidentRepo, _analysis, _incident

pytestmark = pytest.mark.asyncio


async def test_get_daily_report_returns_counts_and_markdown():
    import datetime as dt

    i1 = _incident("GCM", "resolved", dt.datetime(2026, 7, 25, 10, tzinfo=dt.timezone.utc))
    a1 = _analysis(i1.id, "critical", "OOM")
    repo = FakeIncidentRepo([(i1, a1)])
    app.dependency_overrides[get_daily_report] = lambda: DailyReport(
        incidents=repo, chat=FakeChatModel()
    )

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
