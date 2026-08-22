"""App-settings response DTOs — deliberately omits the secret value entirely, never round-tripped
back to the client (same principle as CloudConnectionOut for AWS access keys)."""

from __future__ import annotations

from pydantic import BaseModel


class SettingStatus(BaseModel):
    """`GET /api/settings/{key}` response: whether a value is configured, never the value itself."""

    is_set: bool


class UsageByModelOut(BaseModel):
    model_id: str
    input_tokens: int
    output_tokens: int
    analyses_count: int


class LlmUsageOut(BaseModel):
    """`GET /api/settings/llm-usage` response: real token spend, grouped by model. Excludes cache
    hits (no LLM call made) and providers that don't report usage (currently only claude_cli
    does)."""

    total_input_tokens: int
    total_output_tokens: int
    by_model: list[UsageByModelOut]
