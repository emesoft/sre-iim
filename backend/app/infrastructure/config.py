"""Application settings, loaded from environment variables / .env.

Defaults match the Step 0 brain (`domain/incidents/prompts.py`) so the API behaves the same when it
wraps the analysis. See `.claude/specs/SPEC.md` section 13 for the full env var list.

pydantic-settings docs: https://docs.pydantic.dev/latest/concepts/pydantic_settings/
"""

import os
from dataclasses import dataclass
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---
    app_name: str = "IIM"
    debug: bool = False
    # Comma-separated list of allowed CORS origins (the Vite dev server / nginx).
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # --- Database (Postgres + pgvector) ---
    # asyncpg driver; overridden by DATABASE_URL in compose.
    database_url: str = "postgresql+asyncpg://iim:iim@localhost:5432/iim"

    # --- Analysis pipeline ---
    analysis_mode: str = "single"  # single (RAG single-call) | graph (multi-agent LangGraph)

    # --- Demo mode ---
    # Global switch (not per project): serve synthetic log lines instead of querying a cloud, so
    # the log-search flow works with no AWS account. See `infrastructure/logs/demo_fetcher.py`.
    demo_logs: bool = False

    # --- LLM provider (decision 0016) ---
    llm_provider: str = "bedrock"  # bedrock | deepseek | claude_cli
    max_rounds: int = 2  # critic corrective-retrieval loop cap

    # Bedrock (Claude)
    aws_region: str = "ap-southeast-1"
    model_id: str = "anthropic.claude-3-5-haiku-20241022-v1:0"  # main tier (diagnosis, critic)
    fast_model_id: str = "anthropic.claude-3-5-haiku-20241022-v1:0"  # Haiku tier (triage, etc.)

    # Completion budget for the OpenAI-compatible providers. Generous on purpose: reasoning models
    # (e.g. OpenRouter's gpt-oss) spend this budget on reasoning tokens before emitting any JSON, so
    # a tight cap truncates the answer mid-object and surfaces as "model did not return valid JSON".
    llm_max_tokens: int = 1500

    # DeepSeek (OpenAI-compatible)
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com"

    # Claude Code CLI (local demo only — see infrastructure/llm/claude_cli.py). Runs the `claude`
    # headless CLI as a subprocess, authenticated with a Claude Code subscription token entered on
    # the Settings page, NOT an Anthropic API key. Anthropic's terms restrict subscription OAuth to
    # "ordinary use" and CI, not an always-on backend — this provider is for local/demo use only;
    # switch to `bedrock` or `deepseek` (real API billing) before any production deployment.
    claude_cli_model: str = "sonnet"

    # --- Embedding provider (decision 0016) ---
    embedding_provider: str = "titan"  # titan | jina
    embedding_model: str = "amazon.titan-embed-text-v2:0"
    embedding_dim: int = 1024  # Titan v2 = 1024, Jina jina-embeddings-v3 = 768

    # Jina
    jina_api_key: str | None = None
    jina_base_url: str = "https://api.jina.ai/v1/embeddings"

    # --- Cache ---
    cache_ttl_seconds: int = 1800  # 30 min, matches Step 0 CACHE_TTL_SECONDS

    # --- Ticketing (Azure DevOps) ---
    azdo_org: str | None = None
    azdo_project: str | None = None
    azdo_pat: str | None = None
    azdo_work_item_type: str = "Bug"

    # --- Cloud connections (CloudWatch alarm polling) ---
    secret_encryption_key: str = ""  # Fernet key (44-char urlsafe base64); required to store access keys
    alarm_poll_interval_minutes: int = 60

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()


@dataclass(frozen=True)
class ProjectConfig:
    """Per-project cloud config. Each project (incident `service`) may live on a different
    cloud/account — e.g. GCM on AWS today, another project on Azure/GCP later — so credentials
    and region are resolved per project rather than from one global setting.

    Env convention: `PROJECT_<SERVICE>_CLOUD` (default "aws"); AWS-specific:
    `PROJECT_<SERVICE>_AWS_PROFILE` (an SSO profile name from `~/.aws/config`, e.g.
    "GCM-Prod-ReadOnlyAccess") and `PROJECT_<SERVICE>_AWS_REGION` (falls back to `aws_region`).
    Azure/GCP keys aren't wired yet — `cloud` is already generic so adding them later is additive.
    """

    cloud: str
    aws_profile: str | None = None
    aws_region: str | None = None


def get_project_config(service: str, settings: Settings) -> ProjectConfig:
    """Resolve `service`'s cloud config from `PROJECT_<SERVICE>_*` env vars. A project with no
    dedicated block still works — it falls back to the global AWS region and default credential
    chain — so this is additive, not a required setup step per project."""
    prefix = f"PROJECT_{service.upper()}_"
    return ProjectConfig(
        cloud=os.environ.get(f"{prefix}CLOUD", "aws"),
        aws_profile=os.environ.get(f"{prefix}AWS_PROFILE"),
        aws_region=os.environ.get(f"{prefix}AWS_REGION", settings.aws_region),
    )
