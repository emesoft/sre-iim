"""ChatModel adapters (Bedrock, DeepSeek) with model tiering — implement the domain `ChatModel` port.

`tier="fast"` uses the cheap model for mechanical steps (triage/synthesize); `tier="main"` uses the
stronger model for diagnosis/critic (decision 0011 model tiering). DeepSeek has a single chat model, so
both tiers map to it.
"""

from __future__ import annotations

import asyncio

import httpx
from langchain_aws import ChatBedrockConverse

from app.domain.llm import Tier
from app.infrastructure.config import Settings


class BedrockChatModel:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._clients: dict[str, ChatBedrockConverse] = {}

    def _model_id(self, tier: Tier) -> str:
        return self._settings.fast_model_id if tier == "fast" else self._settings.model_id

    def _client(self, tier: Tier) -> ChatBedrockConverse:
        model_id = self._model_id(tier)
        if model_id not in self._clients:
            self._clients[model_id] = ChatBedrockConverse(
                model=model_id,
                region_name=self._settings.aws_region,
                max_tokens=700,
                temperature=0.2,
            )
        return self._clients[model_id]

    async def complete(self, system: str, user: str, *, tier: Tier = "main") -> str:
        def _invoke() -> str:
            resp = self._client(tier).invoke([("system", system), ("human", user)])
            return resp.content if isinstance(resp.content, str) else str(resp.content)

        return await asyncio.to_thread(_invoke)


class DeepSeekChatModel:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def complete(self, system: str, user: str, *, tier: Tier = "main") -> str:
        if not self._settings.deepseek_api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set")
        url = self._settings.deepseek_base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self._settings.deepseek_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": self._settings.llm_max_tokens,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self._settings.deepseek_api_key}"}
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]
