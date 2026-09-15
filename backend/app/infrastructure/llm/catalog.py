"""The LLM catalog: the one place that knows which model providers exist and what each one needs.

Same idea as `integrations/registry.py`, one level up: providers are *declared data*, so the
Settings form builds its fields from the declaration and adding a provider is one entry plus an
adapter — no call site learns a new vendor name.

It is deliberately separate from the integrations registry because the two answer different
questions. An integration is per project ("which AWS account does gcm poll?"); the analysis model
is one app-wide choice. Forcing model providers into `integrations` would mean inventing a project
for them, and a fake row is worse than a second small table.

Bedrock is the exception that connects the two: it can borrow the AWS credentials of an existing
integration rather than asking for a second copy of keys the app already holds.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.incidents.ports import Analyzer
from app.domain.llm import ChatModel

BEDROCK = "bedrock"
ANTHROPIC = "anthropic"
CLAUDE_CLI = "claude_cli"
OPENAI_COMPATIBLE = "deepseek"


@dataclass(frozen=True)
class LlmField:
    """One input on the Settings form. `secret` values are write-only: stored encrypted, never
    returned, and left untouched when the form is submitted without them."""

    name: str
    label: str
    secret: bool = False
    required: bool = True
    placeholder: str = ""
    help: str = ""
    #: Renders as a picker over the app's AWS integrations instead of a text box.
    source: str | None = None
    #: Known-good values, offered as a dropdown so the common case involves no typing at all.
    #: Never enforced: a model released this morning must be usable this morning, and an allowlist
    #: in a shipped container would make the app stale on someone else's release schedule. The
    #: "never tested" badge is what catches a typo in a value picked from outside this list.
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class LlmProviderSpec:
    key: str
    label: str
    notes: str
    fields: tuple[LlmField, ...] = ()
    defaults: dict[str, str] = field(default_factory=dict)


#: API model ids. Kept here rather than in the frontend so one edit updates every form.
_API_MODELS = (
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
)
#: Bedrock model ids, which are neither API ids nor CLI aliases — and often need a regional
#: inference-profile prefix (us./eu./apac.) that varies by account and region, so this list is a
#: starting point rather than anything close to complete.
_BEDROCK_MODELS = (
    "anthropic.claude-3-5-haiku-20241022-v1:0",
    "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "apac.anthropic.claude-sonnet-4-20250514-v1:0",
)

#: What the Claude Code CLI accepts — short aliases, not API ids. Mixing the two up is the typo
#: this dropdown exists to prevent.
_CLI_MODELS = ("opus", "sonnet", "haiku")

_MODEL = LlmField(
    name="model",
    label="Model",
    placeholder="claude-opus-5",
    options=_API_MODELS,
    help="Used for the analysis itself, and for the diagnose/critic steps in graph mode.",
)
_FAST_MODEL = LlmField(
    name="fast_model",
    label="Fast model",
    required=False,
    options=_API_MODELS,
    help="Graph mode only: the cheaper model for the mechanical triage and synthesis steps.",
)

CATALOG: dict[str, LlmProviderSpec] = {
    ANTHROPIC: LlmProviderSpec(
        key=ANTHROPIC,
        label="Anthropic API",
        notes="A plain API key, billed per token. The normal choice for a deployed instance.",
        fields=(
            LlmField(
                name="api_key",
                label="API key",
                secret=True,
                placeholder="sk-ant-api03-…",
                help="From console.anthropic.com. Stored encrypted; never shown again.",
            ),
            _MODEL,
            _FAST_MODEL,
        ),
        defaults={"model": "claude-opus-5", "fast_model": "claude-haiku-4-5-20251001"},
    ),
    BEDROCK: LlmProviderSpec(
        key=BEDROCK,
        label="AWS Bedrock",
        notes="Claude through your own AWS account — no separate vendor contract or key.",
        fields=(
            LlmField(
                name="aws_integration_id",
                label="AWS credentials",
                required=False,
                source="aws_integration",
                help=(
                    "Reuse an AWS integration you've already configured. Leave empty to use the "
                    "credentials in the container's environment or its instance role."
                ),
            ),
            LlmField(
                name="region",
                label="Region",
                required=False,
                placeholder="us-east-1",
                help="Bedrock region. Some regions need an inference-profile prefix on the model id.",
            ),
            LlmField(
                name="model",
                label="Model",
                placeholder="anthropic.claude-3-5-haiku-20241022-v1:0",
                options=_BEDROCK_MODELS,
                help=(
                    "A Bedrock model id, not an API model name. Some regions require an "
                    "inference-profile prefix (us./eu./apac.) — check the Bedrock console."
                ),
            ),
            LlmField(
                name="fast_model",
                label="Fast model",
                required=False,
                options=_BEDROCK_MODELS,
                help="Graph mode only: the cheaper model for triage and synthesis.",
            ),
        ),
    ),
    CLAUDE_CLI: LlmProviderSpec(
        key=CLAUDE_CLI,
        label="Claude Code subscription (local demo)",
        notes=(
            "Spends a Claude Code subscription through the CLI on this machine. Not for a "
            "deployed instance — it needs the CLI installed and signed in."
        ),
        fields=(
            LlmField(
                name="token",
                label="OAuth token",
                secret=True,
                placeholder="sk-ant-oat-…",
                help="Run `claude setup-token` on this machine and paste the result.",
            ),
            LlmField(
                name="model",
                label="Model",
                required=False,
                placeholder="sonnet",
                options=_CLI_MODELS,
                help="The CLI takes short aliases, not API model ids.",
            ),
        ),
        defaults={"model": "sonnet"},
    ),
    OPENAI_COMPATIBLE: LlmProviderSpec(
        key=OPENAI_COMPATIBLE,
        label="OpenAI-compatible endpoint",
        notes=(
            "Any /chat/completions API: OpenAI, OpenRouter, DeepSeek, a local vLLM. Set the base "
            "URL to pick which."
        ),
        fields=(
            LlmField(name="api_key", label="API key", secret=True, placeholder="sk-…"),
            LlmField(
                name="base_url",
                label="Base URL",
                placeholder="https://api.openai.com/v1",
                help="Without the /chat/completions suffix.",
            ),
            LlmField(
                name="model",
                label="Model",
                placeholder="gpt-4o",
                options=("gpt-4o", "gpt-4o-mini", "deepseek-chat"),
                help="Whatever the endpoint above serves — the list is a shortcut, not a limit.",
            ),
        ),
        defaults={"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    ),
}


def spec(provider: str) -> LlmProviderSpec:
    try:
        return CATALOG[provider]
    except KeyError as exc:
        raise ValueError(f"unknown LLM provider: {provider!r}") from exc


def secret_names(provider: str) -> tuple[str, ...]:
    return tuple(f.name for f in spec(provider).fields if f.secret)


__all__ = [
    "ANTHROPIC",
    "BEDROCK",
    "CATALOG",
    "CLAUDE_CLI",
    "OPENAI_COMPATIBLE",
    "Analyzer",
    "ChatModel",
    "LlmField",
    "LlmProviderSpec",
    "spec",
    "secret_names",
]
