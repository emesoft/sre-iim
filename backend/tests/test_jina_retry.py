"""Unit tests for the embedder's retry policy — no network.

Every analysis embeds a retrieval query before it reaches the LLM, so this call is a single point
of failure for the whole pipeline. A dropped connection once ended a real analysis outright and
marked the incident `failed` for a blip that succeeded on the very next attempt.
"""

import httpx
import pytest

from app.infrastructure.config import Settings
from app.infrastructure.llm.jina_embedder import JinaEmbedder, JinaEmbedderError

_OK = {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}


def _embedder(monkeypatch, responses) -> tuple[JinaEmbedder, list]:
    """Drives the embedder through a scripted sequence of transport outcomes."""
    calls = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json, headers):
            outcome = responses[len(calls)]
            calls.append(url)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: FakeClient())
    # No real sleeping: the policy is what's under test, not the wall clock.
    monkeypatch.setattr("app.infrastructure.llm.jina_embedder.asyncio.sleep", _noop)
    settings = Settings(jina_api_key="k", embedding_dim=768, secret_encryption_key="x" * 44)
    return JinaEmbedder(settings), calls


async def _noop(_seconds):
    return None


def _response(status: int, payload: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status, json=payload or {}, request=httpx.Request("POST", "https://api.jina.ai/v1/embeddings")
    )


async def test_a_dropped_connection_is_retried_and_succeeds(monkeypatch):
    """The exact failure seen in production: "Server disconnected without sending a response"."""
    dropped = httpx.RemoteProtocolError("Server disconnected without sending a response")
    embedder, calls = _embedder(monkeypatch, [dropped, _response(200, _OK)])
    assert await embedder.embed_query("why did it 5xx") == [0.1, 0.2]
    assert len(calls) == 2


async def test_a_rate_limit_is_retried(monkeypatch):
    embedder, calls = _embedder(monkeypatch, [_response(429), _response(200, _OK)])
    assert await embedder.embed_query("q") == [0.1, 0.2]
    assert len(calls) == 2


async def test_a_server_error_is_retried(monkeypatch):
    embedder, calls = _embedder(monkeypatch, [_response(503), _response(200, _OK)])
    await embedder.embed_query("q")
    assert len(calls) == 2


async def test_a_bad_key_fails_immediately(monkeypatch):
    """Retrying a 401 turns a clear error into a slow one, three times over."""
    embedder, calls = _embedder(monkeypatch, [_response(401), _response(200, _OK)])
    with pytest.raises(httpx.HTTPStatusError):
        await embedder.embed_query("q")
    assert len(calls) == 1


async def test_it_gives_up_rather_than_retrying_forever(monkeypatch):
    dropped = httpx.RemoteProtocolError("boom")
    embedder, calls = _embedder(monkeypatch, [dropped, dropped, dropped])
    with pytest.raises(JinaEmbedderError, match="after 3 attempts"):
        await embedder.embed_query("q")
    assert len(calls) == 3


async def test_a_first_attempt_success_does_not_sleep(monkeypatch):
    embedder, calls = _embedder(monkeypatch, [_response(200, _OK)])
    await embedder.embed_query("q")
    assert len(calls) == 1
