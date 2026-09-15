"""Which model runs an analysis, resolved per project.

The unit of configuration is a **profile**: a named (provider + settings + credential) pair, e.g.
"Anthropic — prod key" or "Bedrock via gcm". One profile is the deployment default; a project may
point at a different one. Projects reference profiles rather than carrying their own copy of a
credential, so one API key serving ten projects is the ordinary case and rotating it is one edit.

That indirection is the whole design. Storing the provider and key *on* each project would mean
the same key written ten times, ten places to rotate it, and no way to answer "what is this key
used for" — which is exactly the shape the teams/roles work had to be untangled from.

Everything is one encrypted JSON blob in the existing `app_settings` table: it is a single
app-wide document, read once per analysis, and encrypting it whole means credentials inside it are
covered without a schema that has to know which fields are secret. The Claude Code OAuth token is
the one exception — it stays under its own long-standing key, which the CLI adapter reads
directly, because two copies of a credential is how they drift.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field, replace

import boto3

from app.domain.incidents.ports import Analyzer
from app.domain.llm import ChatModel
from app.infrastructure.cloud.credential_resolver import CredentialResolver
from app.infrastructure.config import Settings
from app.infrastructure.db.repositories.app_settings import SqlAlchemyAppSettingsRepository
from app.infrastructure.db.repositories.integrations import SqlAlchemyIntegrationRepository
from app.infrastructure.llm.anthropic_analyzer import AnthropicAnalyzer, AnthropicChatModel
from app.infrastructure.llm.bedrock_analyzer import BedrockAnalyzer
from app.infrastructure.llm.catalog import (
    ANTHROPIC,
    BEDROCK,
    CLAUDE_CLI,
    OPENAI_COMPATIBLE,
    secret_names,
    spec,
)
from app.infrastructure.llm.chat import BedrockChatModel, DeepSeekChatModel
from app.infrastructure.llm.claude_cli import ClaudeCliAnalyzer, ClaudeCliChatModel
from app.infrastructure.llm.deepseek_analyzer import DeepSeekAnalyzer
from app.infrastructure.security.encryptor import Encryptor
from app.infrastructure.security.keys import CLAUDE_CLI_TOKEN_KEY, LLM_CONFIG_KEY


class ProfileNotFoundError(Exception):
    def __init__(self, profile_id: str) -> None:
        super().__init__(f"no model profile {profile_id}")
        self.profile_id = profile_id


class LastProfileError(Exception):
    """Deleting the default with nothing to fall back to would leave every analysis without a
    model — refused, the same way the last admin can't be removed."""

    def __init__(self) -> None:
        super().__init__(
            "this is the only model profile — create another before deleting this one"
        )


@dataclass
class LlmProfile:
    """One named way to reach a model."""

    id: str
    name: str
    provider: str
    config: dict[str, str] = field(default_factory=dict)
    secrets: dict[str, str] = field(default_factory=dict)
    #: Outcome of the last Test, or None if it has never been run. A profile that has never
    #: answered is the one real defence against a mistyped model id — the credential can be
    #: perfect and the run still fail on a name nothing validates up front.
    last_test_ok: bool | None = None
    last_test_error: str | None = None
    #: Only for Bedrock borrowing an AWS integration's credentials; None means ambient creds.
    boto_session: boto3.Session | None = None

    def value(self, name: str, fallback: str = "") -> str:
        return (self.config.get(name) or spec(self.provider).defaults.get(name) or fallback).strip()


@dataclass
class LlmSetup:
    """Everything the Settings page and the resolver need, in one read."""

    profiles: list[LlmProfile] = field(default_factory=list)
    default_profile_id: str | None = None
    #: project name -> profile id, for the projects that override the default.
    by_project: dict[str, str] = field(default_factory=dict)

    def get(self, profile_id: str | None) -> LlmProfile | None:
        return next((p for p in self.profiles if p.id == profile_id), None)

    def for_project(
        self, project: str | None, group_profile_id: str | None = None
    ) -> LlmProfile | None:
        """Precedence: the project's own profile, then the requesting group's, then the default.

        The order is the point. A per-project profile says where a customer's data is *allowed* to
        go; a per-group one says who pays. A constraint outranks a preference, so a team's own key
        can never pull a project's incidents out of the account they were promised to stay in.

        A pointer to a profile someone has since deleted falls through rather than failing — a
        stale id must not stop triage.
        """
        if project and (chosen := self.get(self.by_project.get(project))):
            return chosen
        if group_profile_id and (chosen := self.get(group_profile_id)):
            return chosen
        return self.get(self.default_profile_id) or (self.profiles[0] if self.profiles else None)


# --------------------------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------------------------


async def _read_blob(session, encryptor: Encryptor) -> dict:
    raw = await SqlAlchemyAppSettingsRepository(session).get(LLM_CONFIG_KEY)
    if raw is None:
        return {}
    try:
        return _migrate(json.loads(encryptor.decrypt(raw)))
    except Exception:  # noqa: BLE001 - an unreadable blob must not take the app down
        return {}


def _migrate(blob: dict) -> dict:
    """Converts the first, provider-keyed shape (one config per provider, no names, no per-project
    overrides) into profiles. Kept rather than migrated in SQL because the blob is encrypted — a
    data migration would need the app's key anyway, so the app is the right place to do it."""
    if "providers" not in blob:
        return blob
    profiles: dict[str, dict] = {}
    active = None
    for provider, entry in (blob.get("providers") or {}).items():
        pid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"llm-profile-{provider}"))
        profiles[pid] = {
            "name": spec(provider).label,
            "provider": provider,
            "config": entry.get("config", {}),
            "secrets": entry.get("secrets", {}),
        }
        if provider == blob.get("provider"):
            active = pid
    return {"profiles": profiles, "default_profile_id": active, "by_project": {}}


async def _write_blob(session, encryptor: Encryptor, blob: dict) -> None:
    await SqlAlchemyAppSettingsRepository(session).set(
        LLM_CONFIG_KEY, encryptor.encrypt(json.dumps(blob))
    )


async def load_setup(session, encryptor: Encryptor) -> LlmSetup:
    blob = await _read_blob(session, encryptor)
    profiles = [
        LlmProfile(
            id=pid,
            name=entry.get("name") or spec(entry["provider"]).label,
            provider=entry["provider"],
            config=dict(entry.get("config", {})),
            secrets=dict(entry.get("secrets", {})),
            last_test_ok=entry.get("last_test_ok"),
            last_test_error=entry.get("last_test_error"),
        )
        for pid, entry in (blob.get("profiles") or {}).items()
        if entry.get("provider") in CATALOG_KEYS
    ]
    profiles.sort(key=lambda p: p.name.lower())
    return LlmSetup(
        profiles=profiles,
        default_profile_id=blob.get("default_profile_id"),
        by_project=dict(blob.get("by_project") or {}),
    )


CATALOG_KEYS = {ANTHROPIC, BEDROCK, CLAUDE_CLI, OPENAI_COMPATIBLE}


async def save_profile(
    session,
    encryptor: Encryptor,
    *,
    profile_id: str | None,
    name: str,
    provider: str,
    config: dict,
    secrets: dict,
) -> str:
    """Create or update one profile; returns its id.

    Secrets are merged, not replaced: a form submitted without the API key keeps the stored one, so
    editing a model name never demands a credential nobody has to hand — and can't read back.
    """
    spec(provider)  # rejects an unknown provider before anything is written
    blob = await _read_blob(session, encryptor)
    profiles = blob.setdefault("profiles", {})
    if profile_id is not None and profile_id not in profiles:
        raise ProfileNotFoundError(profile_id)
    pid = profile_id or str(uuid.uuid4())
    entry = profiles.setdefault(pid, {})
    entry["name"] = name.strip() or spec(provider).label
    entry["provider"] = provider
    previous = {"provider": entry.get("provider"), "config": entry.get("config")}
    entry["config"] = {k: v for k, v in config.items() if k not in secret_names(provider)}
    entry["secrets"] = {**entry.get("secrets", {}), **{k: v for k, v in secrets.items() if v}}
    # Any edit to what it points at, or a new credential, invalidates the last result — a profile
    # still showing a green tick after someone changed the model id would be worse than no tick.
    if (
        previous != {"provider": provider, "config": entry["config"]}
        or any(v for v in secrets.values())
    ):
        entry.pop("last_test_ok", None)
        entry.pop("last_test_error", None)

    if provider == CLAUDE_CLI and secrets.get("token"):
        # Kept under the key the CLI adapter already reads, and out of the blob entirely.
        await SqlAlchemyAppSettingsRepository(session).set(
            CLAUDE_CLI_TOKEN_KEY, encryptor.encrypt(secrets["token"])
        )
        entry["secrets"].pop("token", None)

    # The first profile becomes the default — otherwise nothing would be in use and every analysis
    # would fall back to the environment while the page insisted a profile existed.
    blob.setdefault("default_profile_id", None)
    if not blob["default_profile_id"]:
        blob["default_profile_id"] = pid
    await _write_blob(session, encryptor, blob)
    return pid


async def delete_profile(session, encryptor: Encryptor, profile_id: str) -> None:
    blob = await _read_blob(session, encryptor)
    profiles = blob.get("profiles") or {}
    if profile_id not in profiles:
        raise ProfileNotFoundError(profile_id)
    if len(profiles) == 1:
        raise LastProfileError()
    profiles.pop(profile_id)
    # Projects pointing here fall back to the default rather than keeping a dangling pointer.
    blob["by_project"] = {
        k: v for k, v in (blob.get("by_project") or {}).items() if v != profile_id
    }
    if blob.get("default_profile_id") == profile_id:
        blob["default_profile_id"] = next(iter(profiles))
    await _write_blob(session, encryptor, blob)


async def set_default(session, encryptor: Encryptor, profile_id: str) -> None:
    blob = await _read_blob(session, encryptor)
    if profile_id not in (blob.get("profiles") or {}):
        raise ProfileNotFoundError(profile_id)
    blob["default_profile_id"] = profile_id
    await _write_blob(session, encryptor, blob)


async def set_project_profile(
    session, encryptor: Encryptor, project: str, profile_id: str | None
) -> None:
    """Point one project at a profile, or clear the override so it follows the default."""
    blob = await _read_blob(session, encryptor)
    by_project = blob.setdefault("by_project", {})
    if profile_id is None:
        by_project.pop(project, None)
    else:
        if profile_id not in (blob.get("profiles") or {}):
            raise ProfileNotFoundError(profile_id)
        by_project[project] = profile_id
    await _write_blob(session, encryptor, blob)


async def stored_secret_names(session, encryptor: Encryptor, profile: LlmProfile) -> list[str]:
    """Which credentials exist for a profile — never their values."""
    names = set(profile.secrets)
    if profile.provider == CLAUDE_CLI and await SqlAlchemyAppSettingsRepository(session).get(
        CLAUDE_CLI_TOKEN_KEY
    ):
        names.add("token")
    return sorted(names)


# --------------------------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------------------------


def _from_environment(settings: Settings) -> LlmProfile:
    """What ran before any of this existed, and what still runs when nothing is configured."""
    provider = settings.llm_provider
    config = {
        BEDROCK: {
            "model": settings.model_id,
            "fast_model": settings.fast_model_id,
            "region": settings.aws_region,
        },
        OPENAI_COMPATIBLE: {
            "model": settings.deepseek_model,
            "base_url": settings.deepseek_base_url,
        },
        CLAUDE_CLI: {"model": settings.claude_cli_model},
    }.get(provider, {})
    secrets = (
        {"api_key": settings.deepseek_api_key}
        if provider == OPENAI_COMPATIBLE and settings.deepseek_api_key
        else {}
    )
    return LlmProfile(
        id="environment",
        name="From the environment",
        provider=provider,
        config={k: v for k, v in config.items() if v},
        secrets=secrets,
    )


async def resolve_profile_credentials(
    session, encryptor: Encryptor, profile: LlmProfile
) -> LlmProfile:
    """Attach anything the profile only references rather than stores — today, the AWS
    integration whose credentials a Bedrock profile borrows."""
    if profile.provider == BEDROCK and profile.config.get("aws_integration_id"):
        integration = await SqlAlchemyIntegrationRepository(session).get(
            uuid.UUID(profile.config["aws_integration_id"])
        )
        if integration is not None:
            # Off-thread: an `sso_oidc` integration resolves over the network, and the resolver
            # refuses to make those blocking calls on the event loop.
            profile.boto_session = await asyncio.to_thread(
                CredentialResolver(encryptor).resolve, integration
            )
    return profile


async def resolve(
    session,
    settings: Settings,
    encryptor: Encryptor,
    project: str | None = None,
    group_profile_id: str | None = None,
) -> LlmProfile:
    """The profile that should analyse this project's incidents, for this requester."""
    setup = await load_setup(session, encryptor)
    profile = setup.for_project(project, group_profile_id) or _from_environment(settings)
    return await resolve_profile_credentials(session, encryptor, profile)


def build_analyzer(profile: LlmProfile, settings: Settings) -> Analyzer:
    if profile.provider == ANTHROPIC:
        return AnthropicAnalyzer(profile.secrets.get("api_key", ""), profile.value("model"))
    if profile.provider == OPENAI_COMPATIBLE:
        return DeepSeekAnalyzer(_openai_settings(profile, settings))
    if profile.provider == CLAUDE_CLI:
        return ClaudeCliAnalyzer(settings)
    return BedrockAnalyzer(
        settings,
        model=profile.value("model"),
        region=profile.value("region"),
        boto_session=profile.boto_session,
    )


def build_chat_model(profile: LlmProfile, settings: Settings) -> ChatModel:
    if profile.provider == ANTHROPIC:
        return AnthropicChatModel(
            profile.secrets.get("api_key", ""), profile.value("model"), profile.value("fast_model")
        )
    if profile.provider == OPENAI_COMPATIBLE:
        return DeepSeekChatModel(_openai_settings(profile, settings))
    if profile.provider == CLAUDE_CLI:
        return ClaudeCliChatModel(settings)
    return BedrockChatModel(
        settings,
        model=profile.value("model"),
        fast_model=profile.value("fast_model"),
        region=profile.value("region"),
        boto_session=profile.boto_session,
    )


async def verify(profile: LlmProfile, settings: Settings) -> None:
    """One real completion through the adapter the app would actually use. A cheaper "is the key
    well-formed" probe would pass on a revoked key, on the wrong region, and on a model the
    account can't call — which are exactly the failures worth catching before an incident does."""
    await build_chat_model(profile, settings).complete(
        "Reply with the single word: ok", "ping", tier="fast"
    )


def model_label(profile: LlmProfile, settings: Settings) -> str:
    """What to record as the model behind an analysis, for the usage table."""
    if profile.provider == CLAUDE_CLI:
        return f"claude-cli:{profile.value('model', settings.claude_cli_model)}"
    return profile.value("model") or settings.model_id


def _openai_settings(profile: LlmProfile, settings: Settings) -> Settings:
    """The OpenAI-compatible adapters read their endpoint off `Settings`; hand them a copy carrying
    the profile's values so the adapter itself needn't know the Settings page exists."""
    return settings.model_copy(
        update={
            "deepseek_api_key": profile.secrets.get("api_key") or settings.deepseek_api_key,
            "deepseek_model": profile.value("model"),
            "deepseek_base_url": profile.value("base_url"),
        }
    )


@dataclass
class AttributedAnalyzer:
    """Stamps the profile that ran an analysis onto its draft.

    Lives here rather than in each adapter because a provider adapter has no idea profiles exist —
    it is handed a key and a model id. Without this the usage table can only group by model, and
    two teams on two keys running claude-opus-5 collapse into one line.
    """

    inner: Analyzer
    profile_name: str

    async def analyze(self, context: dict, evidence=None, reporter=None):
        draft = await self.inner.analyze(context, evidence, reporter=reporter)
        return replace(draft, llm_profile=self.profile_name)


async def record_test_result(
    session, encryptor: Encryptor, profile_id: str, *, ok: bool, error: str | None
) -> None:
    """Remember how the last Test went, so the list can flag what has never answered."""
    blob = await _read_blob(session, encryptor)
    entry = (blob.get("profiles") or {}).get(profile_id)
    if entry is None:
        return
    entry["last_test_ok"] = ok
    entry["last_test_error"] = None if ok else (error or "")[:500]
    await _write_blob(session, encryptor, blob)
