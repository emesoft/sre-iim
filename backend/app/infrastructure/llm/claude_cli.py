"""Claude Code CLI provider — LOCAL DEMO ONLY, not for production.

Runs the `claude` headless CLI (`claude -p`) as a subprocess, authenticated with a Claude Code
subscription token entered on the Settings page (`app_settings` key `claude_cli_token`, see
`app/interface/http/settings.py`) — not an Anthropic API key. This lets the demo use a Claude Pro/
Max subscription instead of paying for API usage.

Deliberately does NOT pass `--bare`: that flag forces API-key-only auth and never reads the OAuth
token (`claude --help`: "Anthropic auth is strictly ANTHROPIC_API_KEY or apiKeyHelper ... OAuth and
keychain are never read"). `--safe-mode` is used instead — it disables CLAUDE.md/skills/plugins/
hooks (avoiding token spend on unrelated project context) while explicitly keeping auth working
normally, confirmed against the installed CLI's own `--help` text and by an empirical test call
(cache_creation_input_tokens dropped from ~24.7k to 0 with --safe-mode, same OAuth session).

Anthropic's terms restrict subscription OAuth to "ordinary use" and CI pipelines, not an always-on
backend service — this provider is explicitly out of scope for any production deployment.
"""

from __future__ import annotations

import asyncio
import json
import os

from app.domain.documents.entities import RetrievedChunk
from app.domain.incidents.entities import AnalysisDraft
from app.domain.incidents.prompts import (
    RETRIEVED_KNOWLEDGE_RULES,
    SYSTEM_PROMPT,
    build_user_message,
)
from app.domain.llm import Tier
from app.infrastructure.config import Settings
from app.infrastructure.db.repositories.app_settings import SqlAlchemyAppSettingsRepository
from app.infrastructure.db.session import SessionLocal
from app.infrastructure.llm.parsing import AnalysisError, parse_analysis
from app.infrastructure.security.encryptor import Encryptor
from app.infrastructure.security.keys import CLAUDE_CLI_TOKEN_KEY


async def _get_token(settings: Settings) -> str:
    """Read + decrypt the Claude Code token stored via the Settings page, in its own short-lived
    session (this module has no request-scoped session to reuse)."""
    async with SessionLocal() as session:
        encrypted = await SqlAlchemyAppSettingsRepository(session).get(CLAUDE_CLI_TOKEN_KEY)
    if encrypted is None:
        raise AnalysisError(
            "Claude Code token is not configured — set it on the Settings page "
            "(run `claude setup-token` to generate one)"
        )
    return Encryptor(settings.secret_encryption_key).decrypt(encrypted)


async def _call_claude_cli(*, prompt: str, system_prompt: str | None, model: str, token: str) -> str:
    """Run `claude -p` headless, authenticated via CLAUDE_CODE_OAUTH_TOKEN, and return the
    response text. Raises RuntimeError on any CLI-reported failure (auth, rate limit, bad model)."""
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--tools", "", "--safe-mode", "--model", model]
    if system_prompt:
        cmd.extend(["--append-system-prompt", system_prompt])

    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # API key would silently outrank the OAuth token
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
    )
    stdout, stderr = await proc.communicate()

    try:
        data = json.loads(stdout.decode())
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"claude CLI returned non-JSON output (exit {proc.returncode}): "
            f"{stdout.decode(errors='replace')[:500]}"
        ) from exc

    if data.get("is_error") or proc.returncode != 0:
        detail = data.get("result") or stderr.decode(errors="replace") or "unknown error"
        raise RuntimeError(f"claude CLI failed: {detail}")

    return data["result"]


class ClaudeCliAnalyzer:
    """Implements the domain `Analyzer` port via the Claude Code CLI (see module docstring)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def analyze(
        self, context: dict, evidence: list[RetrievedChunk] | None = None
    ) -> AnalysisDraft:
        token = await _get_token(self._settings)
        system_prompt = SYSTEM_PROMPT + (RETRIEVED_KNOWLEDGE_RULES if evidence else "")
        content = await _call_claude_cli(
            prompt=build_user_message(context, evidence),
            system_prompt=system_prompt,
            model=self._settings.claude_cli_model,
            token=token,
        )
        parsed = parse_analysis(content)
        return AnalysisDraft(model_id=f"claude-cli:{self._settings.claude_cli_model}", **parsed)


class ClaudeCliChatModel:
    """Implements the domain `ChatModel` port via the Claude Code CLI (graph mode / daily report).
    Claude Code CLI has no separate fast/main model tiers, so both map to the same configured model
    (same simplification `DeepSeekChatModel` makes for its single chat model)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def complete(self, system: str, user: str, *, tier: Tier = "main") -> str:
        token = await _get_token(self._settings)
        return await _call_claude_cli(
            prompt=user, system_prompt=system, model=self._settings.claude_cli_model, token=token
        )
