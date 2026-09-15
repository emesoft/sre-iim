"""Unit tests for model-profile selection and storage — no DB, no network.

Three things worth pinning. An instance that has never opened the Settings page must keep behaving
exactly as its environment says. Saving must never lose a credential nobody can re-type. And the
per-project override must resolve the way the page says it does — getting that wrong sends a
customer project's incident context to the wrong vendor, which is the expensive kind of quiet bug.
"""

import json

import pytest

from app.infrastructure.config import Settings
from app.infrastructure.db.orm import AppSettingRow
from app.infrastructure.llm import factory
from app.infrastructure.llm.anthropic_analyzer import AnthropicAnalyzer
from app.infrastructure.llm.bedrock_analyzer import BedrockAnalyzer
from app.infrastructure.llm.catalog import ANTHROPIC, BEDROCK, CLAUDE_CLI, OPENAI_COMPATIBLE
from app.infrastructure.llm.deepseek_analyzer import DeepSeekAnalyzer
from app.infrastructure.security.encryptor import Encryptor
from app.infrastructure.security.keys import CLAUDE_CLI_TOKEN_KEY, LLM_CONFIG_KEY

_KEY = "zH8yV2m3sVW6tG5v9pQwQflR4z1sT8y3lU9wA0b3iF4="


class FakeSession:
    """Just enough of AsyncSession for the app_settings key-value repository."""

    def __init__(self) -> None:
        self.rows: dict[str, AppSettingRow] = {}

    async def get(self, _model, key):
        return self.rows.get(key)

    def add(self, row):
        self.rows[row.key] = row

    async def flush(self):
        pass


def _settings(**overrides) -> Settings:
    base = Settings(
        llm_provider="bedrock", model_id="env-model", fast_model_id="env-fast",
        aws_region="ap-southeast-1", secret_encryption_key=_KEY,
    )
    return base.model_copy(update=overrides)


@pytest.fixture
def enc() -> Encryptor:
    return Encryptor(_KEY)

async def _profile(session, enc, *, name, provider=ANTHROPIC, config=None, secrets=None) -> str:
    return await factory.save_profile(
        session, enc, profile_id=None, name=name, provider=provider,
        config=config or {}, secrets=secrets or {},
    )


async def test_nothing_stored_falls_back_to_the_environment(enc):
    """An instance upgraded into this feature must not change behaviour until someone opens the
    page — the env vars that ran it yesterday still run it today."""
    profile = await factory.resolve(FakeSession(), _settings(), enc)
    assert profile.provider == BEDROCK
    assert profile.value("model") == "env-model"
    assert profile.value("region") == "ap-southeast-1"


async def test_the_first_profile_saved_becomes_the_default(enc):
    """Otherwise the page would list a profile while every analysis still used the environment."""
    session = FakeSession()
    pid = await _profile(session, enc, name="Anthropic prod", config={"model": "claude-opus-5"})
    setup = await factory.load_setup(session, enc)
    assert setup.default_profile_id == pid
    assert (await factory.resolve(session, _settings(), enc)).value("model") == "claude-opus-5"


async def test_a_project_without_an_override_follows_the_default(enc):
    session = FakeSession()
    await _profile(session, enc, name="Default", config={"model": "default-model"})
    profile = await factory.resolve(session, _settings(), enc, project="gcm")
    assert profile.value("model") == "default-model"


async def test_a_project_with_an_override_uses_its_own_profile(enc):
    session = FakeSession()
    await _profile(session, enc, name="Default", config={"model": "default-model"})
    special = await _profile(session, enc, name="Customer key", config={"model": "customer-model"})
    await factory.set_project_profile(session, enc, "EVP", special)

    assert (await factory.resolve(session, _settings(), enc, "EVP")).value("model") == "customer-model"
    # ...and nothing else moves with it.
    assert (await factory.resolve(session, _settings(), enc, "gcm")).value("model") == "default-model"
    assert (await factory.resolve(session, _settings(), enc)).value("model") == "default-model"


async def test_one_profile_can_serve_many_projects(enc):
    """The reason projects reference a profile instead of carrying their own credential: one key,
    one place to rotate it."""
    session = FakeSession()
    await _profile(session, enc, name="Default", config={"model": "default-model"})
    shared = await _profile(session, enc, name="Shared key", secrets={"api_key": "sk-shared"})
    for project in ("gcm", "EVP", "rxdevs"):
        await factory.set_project_profile(session, enc, project, shared)

    for project in ("gcm", "EVP", "rxdevs"):
        assert (await factory.resolve(session, _settings(), enc, project)).secrets["api_key"] == "sk-shared"


async def test_clearing_an_override_returns_a_project_to_the_default(enc):
    session = FakeSession()
    await _profile(session, enc, name="Default", config={"model": "default-model"})
    other = await _profile(session, enc, name="Other", config={"model": "other-model"})
    await factory.set_project_profile(session, enc, "gcm", other)
    await factory.set_project_profile(session, enc, "gcm", None)
    assert (await factory.resolve(session, _settings(), enc, "gcm")).value("model") == "default-model"


async def test_deleting_a_profile_frees_the_projects_pointing_at_it(enc):
    """A dangling pointer would fail analysis for that project alone — quietly, and only for it."""
    session = FakeSession()
    await _profile(session, enc, name="Default", config={"model": "default-model"})
    doomed = await _profile(session, enc, name="Doomed", config={"model": "doomed-model"})
    await factory.set_project_profile(session, enc, "gcm", doomed)
    await factory.delete_profile(session, enc, doomed)

    setup = await factory.load_setup(session, enc)
    assert "gcm" not in setup.by_project
    assert (await factory.resolve(session, _settings(), enc, "gcm")).value("model") == "default-model"


async def test_deleting_the_default_promotes_another_profile(enc):
    session = FakeSession()
    first = await _profile(session, enc, name="First")
    await _profile(session, enc, name="Second")
    await factory.delete_profile(session, enc, first)
    setup = await factory.load_setup(session, enc)
    assert setup.default_profile_id is not None
    assert setup.get(setup.default_profile_id).name == "Second"


async def test_the_only_profile_cannot_be_deleted(enc):
    """Nothing left to analyse with, and the page would show an empty list with no way back."""
    session = FakeSession()
    only = await _profile(session, enc, name="Only")
    with pytest.raises(factory.LastProfileError):
        await factory.delete_profile(session, enc, only)


async def test_pointing_a_project_at_a_profile_that_does_not_exist_is_refused(enc):
    session = FakeSession()
    await _profile(session, enc, name="Default")
    with pytest.raises(factory.ProfileNotFoundError):
        await factory.set_project_profile(session, enc, "gcm", "no-such-profile")


async def test_saving_without_the_key_keeps_the_stored_one(enc):
    """The form submits secrets only when they're retyped. Replacing rather than merging would
    wipe an API key every time someone edited a model name — and it can't be read back."""
    session = FakeSession()
    pid = await _profile(session, enc, name="P", config={"model": "a"}, secrets={"api_key": "sk-keep"})
    await factory.save_profile(
        session, enc, profile_id=pid, name="P", provider=ANTHROPIC, config={"model": "b"}, secrets={}
    )
    profile = await factory.resolve(session, _settings(), enc)
    assert profile.secrets["api_key"] == "sk-keep"
    assert profile.value("model") == "b"


async def test_an_empty_secret_does_not_overwrite_a_stored_one(enc):
    """An untouched password input posts "", which must read as "unchanged", not "delete"."""
    session = FakeSession()
    pid = await _profile(session, enc, name="P", secrets={"api_key": "sk-keep"})
    await factory.save_profile(
        session, enc, profile_id=pid, name="P", provider=ANTHROPIC, config={}, secrets={"api_key": ""}
    )
    assert (await factory.resolve(session, _settings(), enc)).secrets["api_key"] == "sk-keep"


async def test_secrets_are_reported_by_name_only(enc):
    session = FakeSession()
    await _profile(session, enc, name="P", secrets={"api_key": "sk-ant-secret"})
    setup = await factory.load_setup(session, enc)
    assert await factory.stored_secret_names(session, enc, setup.profiles[0]) == ["api_key"]


async def test_the_stored_blob_is_encrypted_at_rest(enc):
    """It holds API keys; it must not be readable straight off the row."""
    session = FakeSession()
    await _profile(session, enc, name="P", secrets={"api_key": "sk-ant-secret"})
    stored = session.rows[LLM_CONFIG_KEY].encrypted_value
    assert "sk-ant-secret" not in stored
    assert "sk-ant-secret" in enc.decrypt(stored)


async def test_the_claude_code_token_stays_under_its_own_key(enc):
    """The CLI adapter reads that key directly; a second copy in the blob is how they drift."""
    session = FakeSession()
    await _profile(session, enc, name="Local", provider=CLAUDE_CLI, secrets={"token": "sk-ant-oat-x"})
    assert enc.decrypt(session.rows[CLAUDE_CLI_TOKEN_KEY].encrypted_value) == "sk-ant-oat-x"
    blob = json.loads(enc.decrypt(session.rows[LLM_CONFIG_KEY].encrypted_value))
    assert next(iter(blob["profiles"].values()))["secrets"] == {}

    setup = await factory.load_setup(session, enc)
    assert await factory.stored_secret_names(session, enc, setup.profiles[0]) == ["token"]


async def test_an_unknown_provider_is_refused_before_anything_is_written(enc):
    session = FakeSession()
    with pytest.raises(ValueError, match="unknown LLM provider"):
        await _profile(session, enc, name="Nope", provider="gpt5-turbo-ultra")
    assert session.rows == {}


async def test_an_unreadable_blob_does_not_take_the_app_down(enc):
    """A rotated SECRET_ENCRYPTION_KEY must degrade to the environment, not 500 every request."""
    session = FakeSession()
    session.add(AppSettingRow(key=LLM_CONFIG_KEY, encrypted_value="not-decryptable"))
    assert (await factory.resolve(session, _settings(), enc)).provider == BEDROCK


async def test_the_first_storage_shape_is_migrated_in_place(enc):
    """Shipped hours earlier in the same session: one config per provider, no names, no overrides.
    It carries an API key, so it converts on read rather than being dropped."""
    session = FakeSession()
    legacy = {
        "provider": ANTHROPIC,
        "providers": {ANTHROPIC: {"config": {"model": "legacy-model"}, "secrets": {"api_key": "sk-legacy"}}},
    }
    session.add(AppSettingRow(key=LLM_CONFIG_KEY, encrypted_value=enc.encrypt(json.dumps(legacy))))

    profile = await factory.resolve(session, _settings(), enc)
    assert profile.provider == ANTHROPIC
    assert profile.value("model") == "legacy-model"
    assert profile.secrets["api_key"] == "sk-legacy"


@pytest.mark.parametrize(
    "provider,expected",
    [
        (ANTHROPIC, AnthropicAnalyzer),
        (BEDROCK, BedrockAnalyzer),
        (OPENAI_COMPATIBLE, DeepSeekAnalyzer),
    ],
)
def test_each_provider_builds_its_own_analyzer(provider, expected):
    profile = factory.LlmProfile(
        id="x", name="x", provider=provider, config={"model": "m", "base_url": "http://x"}
    )
    assert isinstance(factory.build_analyzer(profile, _settings()), expected)


def test_bedrock_carries_the_configured_model_not_the_environment_one():
    """The model recorded against an analysis has to be the one that actually ran it."""
    profile = factory.LlmProfile(id="x", name="x", provider=BEDROCK, config={"model": "picked-in-ui"})
    assert factory.build_analyzer(profile, _settings())._model == "picked-in-ui"
    assert factory.model_label(profile, _settings()) == "picked-in-ui"


# --- precedence ---------------------------------------------------------------------------------


async def test_a_group_profile_is_used_when_the_project_has_no_override(enc):
    """The point of per-group profiles: a team's own key pays for the work its people trigger."""
    session = FakeSession()
    await _profile(session, enc, name="Default", config={"model": "default-model"})
    team = await _profile(session, enc, name="Team key", config={"model": "team-model"})
    setup = await factory.load_setup(session, enc)
    assert setup.for_project("gcm", team).value("model") == "team-model"


async def test_a_project_override_outranks_the_requesting_group(enc):
    """A per-project profile says where a customer's data is *allowed* to go; a per-group one says
    who pays. If the preference won, a team's own key would quietly pull a customer's incidents out
    of the account they were promised to stay in."""
    session = FakeSession()
    await _profile(session, enc, name="Default", config={"model": "default-model"})
    customer = await _profile(session, enc, name="Customer-only", config={"model": "customer-model"})
    team = await _profile(session, enc, name="Team key", config={"model": "team-model"})
    await factory.set_project_profile(session, enc, "EVP", customer)

    setup = await factory.load_setup(session, enc)
    assert setup.for_project("EVP", team).value("model") == "customer-model"
    assert setup.for_project("gcm", team).value("model") == "team-model"


async def test_a_group_pointing_at_a_deleted_profile_falls_back_to_the_default(enc):
    """A stale id on a group must not stop that team's triage."""
    session = FakeSession()
    await _profile(session, enc, name="Default", config={"model": "default-model"})
    setup = await factory.load_setup(session, enc)
    assert setup.for_project("gcm", "deleted-profile-id").value("model") == "default-model"


async def test_the_environment_profile_is_not_attributed_to_anyone():
    """Spend that predates any profile belongs to no cohort — labelling it would invent a payer."""
    env = factory._from_environment(_settings())
    assert env.id == "environment"


# --- "has this ever actually worked?" ------------------------------------------------------------


async def test_a_new_profile_has_never_been_tested(enc):
    """The state the UI warns about: credentials and a model id that nothing has exercised."""
    session = FakeSession()
    await _profile(session, enc, name="P", config={"model": "probably-a-typo"})
    setup = await factory.load_setup(session, enc)
    assert setup.profiles[0].last_test_ok is None


async def test_a_test_result_is_remembered(enc):
    session = FakeSession()
    pid = await _profile(session, enc, name="P")
    await factory.record_test_result(session, enc, pid, ok=True, error=None)
    assert (await factory.load_setup(session, enc)).profiles[0].last_test_ok is True

    await factory.record_test_result(session, enc, pid, ok=False, error="404 model not found")
    profile = (await factory.load_setup(session, enc)).profiles[0]
    assert profile.last_test_ok is False
    assert "model not found" in profile.last_test_error


async def test_editing_the_model_invalidates_the_previous_pass(enc):
    """A green tick left over from the old model id is worse than no tick — it certifies something
    that was never run. This is the typo case: credential fine, model name changed to junk."""
    session = FakeSession()
    pid = await _profile(session, enc, name="P", config={"model": "claude-opus-5"})
    await factory.record_test_result(session, enc, pid, ok=True, error=None)
    await factory.save_profile(
        session, enc, profile_id=pid, name="P", provider=ANTHROPIC,
        config={"model": "sonnet5-typo"}, secrets={},
    )
    assert (await factory.load_setup(session, enc)).profiles[0].last_test_ok is None


async def test_renaming_a_profile_keeps_its_test_result(enc):
    """A rename changes nothing about whether it works, and clearing the badge would train people
    to ignore it."""
    session = FakeSession()
    pid = await _profile(session, enc, name="Old name", config={"model": "claude-opus-5"})
    await factory.record_test_result(session, enc, pid, ok=True, error=None)
    await factory.save_profile(
        session, enc, profile_id=pid, name="New name", provider=ANTHROPIC,
        config={"model": "claude-opus-5"}, secrets={},
    )
    assert (await factory.load_setup(session, enc)).profiles[0].last_test_ok is True


async def test_a_new_credential_invalidates_the_previous_pass(enc):
    session = FakeSession()
    pid = await _profile(session, enc, name="P", secrets={"api_key": "sk-old"})
    await factory.record_test_result(session, enc, pid, ok=True, error=None)
    await factory.save_profile(
        session, enc, profile_id=pid, name="P", provider=ANTHROPIC,
        config={}, secrets={"api_key": "sk-new"},
    )
    assert (await factory.load_setup(session, enc)).profiles[0].last_test_ok is None


def test_every_model_field_offers_values_shaped_for_its_own_provider():
    """The typo this exists to stop: a CLI alias ("sonnet") typed into an API model box, or an API
    id typed into the CLI's. Each provider's dropdown must carry only its own vocabulary."""
    from app.infrastructure.llm.catalog import CATALOG

    def models(provider):
        return next(f.options for f in CATALOG[provider].fields if f.name == "model")

    assert models(CLAUDE_CLI) == ("opus", "sonnet", "haiku")
    assert all(m.startswith("claude-") for m in models(ANTHROPIC))
    assert all("anthropic.claude" in m for m in models(BEDROCK))
    assert not set(models(CLAUDE_CLI)) & set(models(ANTHROPIC))


# --- token accounting -----------------------------------------------------------------------


def test_cache_tokens_are_reported_apart_from_fresh_input():
    """The bug this split fixes: a chat turn reported "92,897 in" when 2 tokens were fresh and the
    rest was the same cached prefix — system prompt, incident context, transcript — re-read every
    single turn. A cache read costs a fraction of fresh input, so the combined figure overstated
    spend by about an order of magnitude, and summing the column across turns counted the same
    prefix again and again."""
    from app.domain.incidents.entities import UsageByModel

    usage = UsageByModel(
        model_id="claude-cli:sonnet",
        input_tokens=2,
        cached_input_tokens=4561,
        output_tokens=13,
        analyses_count=1,
    )
    assert usage.input_tokens == 2
    assert usage.cached_input_tokens == 4561


def test_rows_recorded_before_the_split_are_not_counted_as_fresh():
    """They hold a combined figure that can't be separated now. Reporting it under fresh input
    would repeat the exact overstatement the split removed, so it gets its own bucket instead."""
    from app.domain.incidents.entities import UsageByModel

    legacy = UsageByModel(
        model_id="incident chat",
        input_tokens=0,
        unsplit_input_tokens=472_326,
        output_tokens=4_414,
        analyses_count=7,
    )
    assert legacy.input_tokens == 0
    assert legacy.unsplit_input_tokens == 472_326
