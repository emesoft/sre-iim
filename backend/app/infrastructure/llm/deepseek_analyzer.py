"""DeepSeek analyzer (OpenAI-compatible chat) — implements the domain `Analyzer` port (decision 0016).

A thin async `httpx` call to `{base_url}/chat/completions`; reuses the domain prompt and the shared
response parser. Lets the pipeline run locally without AWS Bedrock.

DeepSeek API (OpenAI-compatible): https://api-docs.deepseek.com/
"""

from __future__ import annotations

import httpx

from app.domain.documents.entities import RetrievedChunk
from app.domain.incidents.entities import AnalysisDraft
from app.domain.incidents.prompts import (
    RETRIEVED_KNOWLEDGE_RULES,
    SYSTEM_PROMPT,
    build_user_message,
)
from app.infrastructure.config import Settings
from app.infrastructure.llm.parsing import AnalysisError, parse_analysis


class DeepSeekAnalyzer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def analyze(
        self, context: dict, evidence: list[RetrievedChunk] | None = None
    ) -> AnalysisDraft:
        if not self._settings.deepseek_api_key:
            raise AnalysisError("DEEPSEEK_API_KEY is not set")
        url = self._settings.deepseek_base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self._settings.deepseek_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT + (RETRIEVED_KNOWLEDGE_RULES if evidence else "")},
                {"role": "user", "content": build_user_message(context, evidence)},
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
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AnalysisError(f"unexpected DeepSeek response shape: {data}") from exc
        parsed = parse_analysis(content)
        return AnalysisDraft(model_id=self._settings.deepseek_model, **parsed)
