"""App-settings response DTOs — deliberately omits the secret value entirely, never round-tripped
back to the client (same principle as CloudConnectionOut for AWS access keys)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SettingStatus(BaseModel):
    """`GET /api/settings/{key}` response: whether a value is configured, never the value itself."""

    is_set: bool


class UsageByModelOut(BaseModel):
    model_id: str
    #: Cache reads/writes, apart from `input_tokens`. Folded together they read as roughly an order
    #: of magnitude more spend than happened.
    cached_input_tokens: int = 0
    #: Pre-split rows whose input figure still includes the cache; reported apart, never as fresh.
    unsplit_input_tokens: int = 0
    #: Which model profile paid for it. Null for spend that predates profiles.
    llm_profile: str | None = None
    #: "analysis" | "chat" — the two have different reasons for a missing profile.
    source: str = "analysis"
    input_tokens: int
    output_tokens: int
    analyses_count: int


class LlmUsageOut(BaseModel):
    """`GET /api/settings/llm-usage` response: real token spend, grouped by model. Excludes cache
    hits (no LLM call made) and providers that don't report usage (currently only claude_cli
    does)."""

    total_input_tokens: int
    total_cached_input_tokens: int = 0
    total_output_tokens: int
    by_model: list[UsageByModelOut]


class LlmFieldOut(BaseModel):
    """One input the Settings form should render for a provider. The form is built from these
    rather than hard-coded, so adding a provider needs no frontend change."""

    name: str
    label: str
    secret: bool = False
    required: bool = True
    placeholder: str = ""
    help: str = ""
    #: "aws_integration" renders a picker over the configured AWS integrations.
    source: str | None = None
    #: Known-good values for a dropdown. Not a whitelist — anything else is still accepted.
    options: list[str] = Field(default_factory=list)


class LlmProviderOut(BaseModel):
    key: str
    label: str
    notes: str
    fields: list[LlmFieldOut] = Field(default_factory=list)
    defaults: dict[str, str] = Field(default_factory=dict)


class LlmProfileOut(BaseModel):
    """One named way to reach a model. `secret_names` says which credentials exist; values never
    leave the server."""

    id: str
    name: str
    provider: str
    config: dict[str, str] = Field(default_factory=dict)
    secret_names: list[str] = Field(default_factory=list)
    #: None means it has never been tested — which is the state worth warning about.
    last_test_ok: bool | None = None
    last_test_error: str | None = None


class LlmProfileIdOut(BaseModel):
    id: str


class LlmSetupOut(BaseModel):
    profiles: list[LlmProfileOut] = Field(default_factory=list)
    default_profile_id: str | None = None
    #: project -> profile id, for the projects that override the default.
    by_project: dict[str, str] = Field(default_factory=dict)
    providers: list[LlmProviderOut] = Field(default_factory=list)
