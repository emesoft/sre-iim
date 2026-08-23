"""Unit tests for the Azure DevOps ticket client. No network — monkeypatches httpx."""

import pytest

from app.infrastructure.tickets.ado_client import AdoApiError, AdoTicketClient

pytestmark = pytest.mark.asyncio


class _FakeResponse:
    def __init__(self, json_data=None, status_code=200):
        self._json = json_data or {}
        self.status_code = status_code
        self.text = str(json_data or "")

    @property
    def is_success(self):
        return self.status_code < 400

    def json(self):
        return self._json


class _FakeAsyncClient:
    def __init__(self, calls, *, get_response=None, post_response=None):
        self._calls = calls
        self._get_response = get_response or _FakeResponse()
        self._post_response = post_response or _FakeResponse(
            {"id": 123, "_links": {"html": {"href": "https://dev.azure.com/org/proj/_workitems/edit/123"}}}
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        self._calls.append(("GET", url))
        return self._get_response

    async def post(self, url, json, headers):
        self._calls.append(("POST", url, json))
        return self._post_response


async def test_create_ticket_returns_the_html_link(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.infrastructure.tickets.ado_client.httpx.AsyncClient",
        lambda **kw: _FakeAsyncClient(calls),
    )

    client = AdoTicketClient(org="my-org", project="my-project", pat="secret-pat")
    url = await client.create_ticket("title", "description")

    assert url == "https://dev.azure.com/org/proj/_workitems/edit/123"
    assert calls[0][0] == "POST"
    assert "my-org/my-project" in calls[0][1]
    assert "$Bug" in calls[0][1]

    patch = calls[0][2]
    repro_ops = [op for op in patch if op["path"] == "/fields/Microsoft.VSTS.TCM.ReproSteps"]
    assert len(repro_ops) == 1
    assert repro_ops[0]["value"] == "description"


async def test_create_ticket_does_not_set_repro_steps_for_non_bug_types(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.infrastructure.tickets.ado_client.httpx.AsyncClient",
        lambda **kw: _FakeAsyncClient(calls),
    )

    client = AdoTicketClient(
        org="my-org", project="my-project", pat="secret-pat", work_item_type="Task"
    )
    await client.create_ticket("title", "description")

    patch = calls[0][2]
    assert not any(op["path"] == "/fields/Microsoft.VSTS.TCM.ReproSteps" for op in patch)
    assert any(op["path"] == "/fields/System.Description" for op in patch)


async def test_verify_raises_on_a_bad_response(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.infrastructure.tickets.ado_client.httpx.AsyncClient",
        lambda **kw: _FakeAsyncClient(calls, get_response=_FakeResponse(status_code=401)),
    )

    client = AdoTicketClient(org="my-org", project="my-project", pat="bad-pat")
    with pytest.raises(AdoApiError):
        await client.verify()


async def test_create_ticket_raises_ado_api_error_with_the_response_message(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.infrastructure.tickets.ado_client.httpx.AsyncClient",
        lambda **kw: _FakeAsyncClient(
            calls,
            post_response=_FakeResponse(
                {"message": "TF401347: Work item type Bug does not exist"}, status_code=400
            ),
        ),
    )

    client = AdoTicketClient(org="my-org", project="my-project", pat="secret-pat")
    with pytest.raises(AdoApiError, match="Work item type Bug does not exist"):
        await client.create_ticket("title", "description")


async def test_create_ticket_adds_a_related_link_when_given_a_related_url(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.infrastructure.tickets.ado_client.httpx.AsyncClient",
        lambda **kw: _FakeAsyncClient(calls),
    )

    client = AdoTicketClient(org="my-org", project="my-project", pat="secret-pat")
    await client.create_ticket(
        "title",
        "description",
        related_url="https://dev.azure.com/my-org/my-project/_workitems/edit/42",
    )

    patch = calls[0][2]
    relation_ops = [op for op in patch if op["path"] == "/relations/-"]
    assert len(relation_ops) == 1
    assert relation_ops[0]["value"]["rel"] == "System.LinkTypes.Related"
    assert relation_ops[0]["value"]["url"] == "https://dev.azure.com/my-org/_apis/wit/workItems/42"


async def test_verify_succeeds_on_a_good_response(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.infrastructure.tickets.ado_client.httpx.AsyncClient",
        lambda **kw: _FakeAsyncClient(calls),
    )

    client = AdoTicketClient(org="my-org", project="my-project", pat="good-pat")
    await client.verify()  # does not raise

    assert calls[0][0] == "GET"
    assert "my-org" in calls[0][1] and "my-project" in calls[0][1]
