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

**But `--safe-mode` also disables MCP servers**, which is why only the analysis path uses it. The
chat path needs its four MCP tools and reaches the same token saving with
`--tools "" --setting-sources "" --disable-slash-commands` — see the comment in `_run_turn`.

Anthropic's terms restrict subscription OAuth to "ordinary use" and CI pipelines, not an always-on
backend service — this provider is explicitly out of scope for any production deployment.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from dataclasses import dataclass

from app.domain.documents.entities import RetrievedChunk
from app.domain.incidents.entities import AnalysisDraft, ChatMessage, ChatTurnResult
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


# What the MCP tool subprocess actually needs — deliberately NOT the full parent environment (see
# _run_turn). PROJECT_<SERVICE>_* vars are added per-call since their names depend on the service.
#
# DATABASE_URL and SECRET_ENCRYPTION_KEY are here because all four tools read the project's
# `integrations` row and decrypt its credentials. They were withheld as a precaution, and the
# precaution was silent: both have plausible-looking defaults (`...@localhost:5432/iim` and `""`),
# so the subprocess did not report missing configuration — it reported "Connect call failed", and
# every AWS tool failed 100% of the time while looking like an AWS problem. Withholding them is
# also not much of a boundary: this is our own module, in our own image, and the allowlist already
# forwards AWS_SECRET_ACCESS_KEY. The values reach it through a 0600 temp file that is unlinked
# after the turn, never through argv.
_MCP_TOOL_ENV_ALLOWLIST = {
    "PATH",
    "HOME",
    "DEMO_LOGS",
    "DATABASE_URL",
    "SECRET_ENCRYPTION_KEY",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_PROFILE",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
}


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


@dataclass(frozen=True)
class _CliResult:
    text: str
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None


async def _run_cli(cmd: list[str], token: str) -> _CliResult:
    """Run an already-built `claude` command, authenticated via CLAUDE_CODE_OAUTH_TOKEN, and
    parse the JSON output into a _CliResult. Raises RuntimeError on any CLI-reported failure."""
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

    usage = data.get("usage") or {}
    # Reported apart, not summed. `input_tokens` alone undercounts badly — --safe-mode caches most
    # of the prompt (system prompt, incident context, transcript) across calls in the same OAuth
    # session — but adding the cached figures into it overcounts *cost* by roughly the same margin,
    # since a cache read is a fraction of the price of fresh input and the same cached prefix is
    # re-read on every single turn. Two numbers are the only honest answer.
    has_usage = "input_tokens" in usage
    cached = (
        usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
        if has_usage
        else None
    )
    return _CliResult(
        text=data["result"],
        input_tokens=usage.get("input_tokens") if has_usage else None,
        cached_input_tokens=cached,
        output_tokens=usage.get("output_tokens"),
    )


async def _call_claude_cli(
    *, prompt: str, system_prompt: str | None, model: str, token: str
) -> _CliResult:
    """Run `claude -p` headless with no tools at all — analysis / graph-mode / daily-report calls
    never need Claude to act autonomously (only incident chat does; see ClaudeCliChat below)."""
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--tools", "", "--safe-mode", "--model", model]
    if system_prompt:
        cmd.extend(["--append-system-prompt", system_prompt])
    return await _run_cli(cmd, token)


async def verify_claude_cli_token(settings: Settings) -> tuple[bool, str | None]:
    """Test whether the stored Claude Code token still works — a real, minimal `claude -p` call,
    not just "is a value stored" (a token can be saved but expired/revoked). Never raises; reports
    the failure reason instead, so the Settings page can show it (e.g. "run `claude setup-token`
    again")."""
    try:
        token = await _get_token(settings)
        await _call_claude_cli(
            prompt="Reply with exactly: OK", system_prompt=None, model=settings.claude_cli_model, token=token
        )
        return True, None
    except Exception as exc:  # noqa: BLE001 - reported to the caller as a test result, not raised
        return False, str(exc)


class ClaudeCliAnalyzer:
    """Implements the domain `Analyzer` port via the Claude Code CLI (see module docstring)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def analyze(
        self, context: dict, evidence: list[RetrievedChunk] | None = None
    ) -> AnalysisDraft:
        token = await _get_token(self._settings)
        system_prompt = SYSTEM_PROMPT + (RETRIEVED_KNOWLEDGE_RULES if evidence else "")
        result = await _call_claude_cli(
            prompt=build_user_message(context, evidence),
            system_prompt=system_prompt,
            model=self._settings.claude_cli_model,
            token=token,
        )
        parsed = parse_analysis(result.text)
        return AnalysisDraft(
            model_id=f"claude-cli:{self._settings.claude_cli_model}",
            input_tokens=result.input_tokens,
            cached_input_tokens=result.cached_input_tokens,
            output_tokens=result.output_tokens,
            **parsed,
        )


class ClaudeCliChatModel:
    """Implements the domain `ChatModel` port via the Claude Code CLI (graph mode / daily report).
    Claude Code CLI has no separate fast/main model tiers, so both map to the same configured model
    (same simplification `DeepSeekChatModel` makes for its single chat model)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def complete(self, system: str, user: str, *, tier: Tier = "main") -> str:
        token = await _get_token(self._settings)
        result = await _call_claude_cli(
            prompt=user, system_prompt=system, model=self._settings.claude_cli_model, token=token
        )
        return result.text


# Naming the tools matters more than it looks. Told only that it "has tools", the CLI answered a
# question about ECS task counts by explaining it had no AWS CLI available and listing the
# `aws ecs describe-services` commands the reader should run and paste back — a checklist, while the
# app was already holding credentials that could answer it. The rule below is deliberately blunt.
_CHAT_SYSTEM_PROMPT = """You are a senior SRE helping investigate one specific incident in a chat conversation. \
You have the incident's full raw context below. Answer the user's questions grounded in that context.

You can investigate this incident's own AWS account directly. Use the tools rather than telling the user \
to go and run commands themselves — if a question can be answered by a tool, answer it:
- describe_alarm(alarm_name): an alarm's threshold, missing-data handling, dimensions, and CloudWatch's own \
  state reason. Start here; the dimensions tell you which resource the alarm watches (never infer that from \
  the alarm's name).
- metric_datapoints(namespace, metric_name, dimensions, minutes): the real numbers behind a metric. \
  Zero datapoints is an answer, and a different one from a low value.
- ecs_service_state(cluster, service): desired/running/pending task counts, why recent tasks stopped, and \
  recent service events.
- fetch_logs(log_group, start, end, filter_pattern): real log lines for this incident's service.

Never ask the user to run an AWS command you could run yourself, and never ask for AWS credentials — you \
already act as this project's own integration. If a tool reports no integration is configured, say that \
plainly. Never invent log content, metrics, or events that aren't in the context or in a tool result. \
Be concise and direct, like an engineer working the incident live."""


#: Read-only investigation tools, as registered in mcp_log_tool.py.
_ALLOWED_TOOLS = (
    "mcp__iim-tools__fetch_logs",
    "mcp__iim-tools__describe_alarm",
    "mcp__iim-tools__metric_datapoints",
    "mcp__iim-tools__ecs_service_state",
)


def _chat_system_prompt(context: dict) -> str:
    return f"{_CHAT_SYSTEM_PROMPT}\n\nIncident context:\n{json.dumps(context, indent=2, default=str)}"


class ClaudeCliChat:
    """Runs one incident-chat turn through the Claude Code CLI's own agentic tool-use loop (an
    MCP server exposing `fetch_logs` plus the AWS lookups, see `mcp_log_tool.py`) — distinct from `ClaudeCliChatModel`,
    which returns plain text with no tools enabled. Only this class needs tools, because incident
    chat is the first feature where Claude must be able to act autonomously mid-call (design spec
    .claude/specs/2026-08-23-incident-chat-design.md)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send(
        self,
        *,
        service: str,
        context: dict,
        message: str,
        claude_session_id: uuid.UUID,
        is_new_session: bool,
        history: list[ChatMessage] | None = None,  # noqa: ARG002 - the CLI keeps its own session
    ) -> ChatTurnResult:
        token = await _get_token(self._settings)
        # The full incident context only needs to be told to Claude once: --resume replays the
        # prior turns (including that first system prompt) from Claude Code's own session storage,
        # so re-appending it on every turn would just duplicate it into the prompt each time,
        # growing token usage turn over turn for no benefit.
        new_session_system_prompt = _chat_system_prompt(context)
        try:
            if is_new_session:
                # Always mint a fresh id for a "new session" attempt, ignoring the caller's id: if
                # a prior failed attempt got far enough to register this session id with Claude
                # Code's local session storage before erroring, a retry reusing the SAME id could
                # be rejected as already-registered, permanently wedging this incident's chat.
                session_id = uuid.uuid4()
            else:
                session_id = claude_session_id
            result = await self._run_turn(
                service=service,
                message=message,
                system_prompt=new_session_system_prompt if is_new_session else None,
                token=token,
                session_id=session_id,
                resume=not is_new_session,
            )
            return ChatTurnResult(
                text=result.text,
                input_tokens=result.input_tokens,
                cached_input_tokens=result.cached_input_tokens,
                output_tokens=result.output_tokens,
                claude_session_id=session_id,
            )
        except RuntimeError:
            if is_new_session:
                raise  # a fresh session failing outright is a real error, not a stale-resume problem
            # --resume pointed at a session the CLI no longer has (e.g. the backend container was
            # recreated between turns) — start a fresh session rather than hard-failing the chat, so
            # it needs the full context again just like any other new session.
            new_session_id = uuid.uuid4()
            result = await self._run_turn(
                service=service,
                message=message,
                system_prompt=new_session_system_prompt,
                token=token,
                session_id=new_session_id,
                resume=False,
            )
            return ChatTurnResult(
                text=result.text,
                input_tokens=result.input_tokens,
                cached_input_tokens=result.cached_input_tokens,
                output_tokens=result.output_tokens,
                claude_session_id=new_session_id,
            )

    async def _run_turn(
        self,
        *,
        service: str,
        message: str,
        system_prompt: str | None,
        token: str,
        session_id: uuid.UUID,
        resume: bool,
    ) -> _CliResult:
        # The MCP server subprocess is spawned BY `claude`, not by us. It only fetches logs
        # (mcp_log_tool.py / resolve_log_fetcher / CloudWatchLogFetcher), so it gets an explicit
        # allowlist of what that needs — not the full parent environment, which carries
        # DATABASE_URL, SECRET_ENCRYPTION_KEY, JWT_SECRET_KEY, AWS creds, etc. that this
        # subprocess has no business seeing.
        mcp_env = {
            key: value
            for key, value in os.environ.items()
            if key in _MCP_TOOL_ENV_ALLOWLIST or key.startswith(f"PROJECT_{service.upper()}_")
        }
        mcp_env["IIM_INCIDENT_SERVICE"] = service
        mcp_config_json = json.dumps(
            {
                "mcpServers": {
                    "iim-tools": {
                        "command": "python3",
                        "args": ["-m", "app.infrastructure.llm.mcp_log_tool"],
                        "env": mcp_env,
                    }
                }
            }
        )
        # Written to a file rather than passed inline: an inline --mcp-config value (which embeds
        # this env dict) would be visible to any other process on the host via /proc/<pid>/cmdline
        # or `ps aux` — a credential leak even with the trimmed allowlist above.
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, prefix="iim-mcp-config-"
        ) as f:
            mcp_config_path = f.name
            f.write(mcp_config_json)
        os.chmod(mcp_config_path, 0o600)
        try:
            cmd = [
                "claude", "-p", message,
                "--output-format", "json",
                "--mcp-config", mcp_config_path,
                "--strict-mcp-config",
                # Every tool must be listed here as well as registered in the MCP server: one
                # missing name is not an error, it just silently isn't callable, and the model then
                # explains it has no way to check and hands the user a list of commands to run.
                "--allowedTools", ",".join(_ALLOWED_TOOLS),
                # NOT `--safe-mode` here, though the analysis path still uses it. Its help text
                # reads "all customizations ... disabled" and that list includes **MCP servers**,
                # so passing it alongside --mcp-config silently won every time: the four tools were
                # registered, allow-listed and never loaded. Chat answered AWS questions by saying
                # it had no way to look and pasting commands for the reader to run — the exact
                # failure the tools exist to prevent, with no error anywhere. Measured, one turn:
                #
                #   --safe-mode                      iim-tools NO   Bash YES   prefix ~29k
                #   (nothing)                        iim-tools YES  Bash YES   prefix ~33k
                #   --tools "" --setting-sources ""  iim-tools YES  Bash NO    prefix ~9.8k
                #
                # The replacement is cheaper as well as correct, and it is what actually removes
                # Bash/Edit/Write from an incident chat — `--safe-mode` never did.
                "--tools", "",
                "--setting-sources", "",
                "--disable-slash-commands",
                "--model", self._settings.claude_cli_model,
            ]
            if system_prompt is not None:
                cmd.extend(["--append-system-prompt", system_prompt])
            cmd.extend(["--resume" if resume else "--session-id", str(session_id)])
            return await _run_cli(cmd, token)
        finally:
            os.unlink(mcp_config_path)
