"""Jina embeddings adapter — implements the domain `Embedder` port (decision 0016).

Async `httpx` call to the Jina embeddings API. Uses `jina-embeddings-v3` task types
(`retrieval.passage` for stored chunks, `retrieval.query` for queries) and requests the configured
output dimension (768 by default). Lets RAG run locally without AWS Titan.

Jina Embeddings API: https://jina.ai/embeddings/
"""

from __future__ import annotations

import asyncio

import httpx

from app.infrastructure.config import Settings


class JinaEmbedderError(RuntimeError):
    pass


#: Three attempts over ~3s. Long enough to ride out a dropped connection, short enough that a real
#: outage still surfaces while someone is looking at the screen.
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 1.0


class JinaEmbedder:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(list(texts), task="retrieval.passage")

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed([text], task="retrieval.query")
        return vectors[0]

    async def _embed(self, texts: list[str], task: str) -> list[list[float]]:
        if not self._settings.jina_api_key:
            raise JinaEmbedderError("JINA_API_KEY is not set")
        payload = {
            "model": self._settings.embedding_model,
            "task": task,
            "dimensions": self._settings.embedding_dim,
            "input": texts,
        }
        headers = {"Authorization": f"Bearer {self._settings.jina_api_key}"}
        data = await self._post_with_retry(payload, headers)
        try:
            items = sorted(data["data"], key=lambda d: d["index"])
        except (KeyError, TypeError) as exc:
            raise JinaEmbedderError(f"unexpected Jina response shape: {data}") from exc
        return [item["embedding"] for item in items]

    async def _post_with_retry(self, payload: dict, headers: dict) -> dict:
        """Retry the transport-level failures only.

        Every analysis embeds a retrieval query before it reaches the LLM, so this one call is a
        single point of failure for the whole pipeline — and a dropped connection ("Server
        disconnected without sending a response") ended an analysis outright, marking the incident
        `failed` for a blip that succeeds on the next try.

        Deliberately narrow: connection errors and 429/5xx are worth another attempt, a 401 or a
        malformed request is not. Retrying those would turn a clear error into a slow one.
        """
        last: Exception | None = None
        for attempt in range(_RETRY_ATTEMPTS):
            if attempt:
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.post(
                        self._settings.jina_base_url, json=payload, headers=headers
                    )
                if resp.status_code == 429 or resp.status_code >= 500:
                    last = JinaEmbedderError(
                        f"Jina returned HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.TransportError as exc:  # connect/read/write/protocol — never a 4xx
                last = exc
        raise JinaEmbedderError(
            f"Jina embedding failed after {_RETRY_ATTEMPTS} attempts: {last}"
        ) from last
