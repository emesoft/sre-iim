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
    def __init__(
        self,
        settings: Settings,
        *,
        model: str | None = None,
        fast_model: str | None = None,
        region: str | None = None,
        boto_session=None,
    ) -> None:
        self._settings = settings
        self._model = model or settings.model_id
        self._fast_model = fast_model or settings.fast_model_id
        self._region = region or settings.aws_region
        self._boto_session = boto_session
        self._clients: dict[str, ChatBedrockConverse] = {}

    def _model_id(self, tier: Tier) -> str:
        return self._fast_model if tier == "fast" else self._model

    def _client(self, tier: Tier) -> ChatBedrockConverse:
        model_id = self._model_id(tier)
        if model_id not in self._clients:
            kwargs = {}
            if self._boto_session is not None:
                kwargs["client"] = self._boto_session.client(
                    "bedrock-runtime", region_name=self._region
                )
            self._clients[model_id] = ChatBedrockConverse(
                model=model_id,
                region_name=self._region,
                max_tokens=700,
                temperature=0.2,
                **kwargs,
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
