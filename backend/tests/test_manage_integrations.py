"""Unit tests for ManageIntegrations — no DB, no network.

The interesting behaviour is validation against the registry's declaration (rather than against a
hard-coded list of clouds) and secret handling, which is where a mistake either stores an unusable
integration or loses a credential nobody can re-type.
"""

import uuid

import pytest

from app.application.integrations.manage import InvalidIntegrationError, ManageIntegrations
from app.domain.integrations.entities import ALARMS, COST, LOGS, TICKETS, Integration
from app.infrastructure.integrations.registry import ProviderRegistry
from app.infrastructure.security.encryptor import Encryptor

_KEY = "zH8yV2m3sVW6tG5v9pQwQflR4z1sT8y3lU9wA0b3iF4="
_AWS_CONFIG = {"region": "us-east-1", "auth_type": "access_key"}


class FakeRepo:
    def __init__(self, rows=()):
        self.rows = {r.id: r for r in rows}

    async def add(self, integration):
        integration.id = uuid.uuid4()
        self.rows[integration.id] = integration
        return integration

    async def get(self, integration_id):
        return self.rows.get(integration_id)

    async def update(self, integration):
        self.rows[integration.id] = integration
        return integration


class FakeUnitOfWork:
    async def commit(self):
        pass

    async def rollback(self):
        pass


def _manager(repo=None) -> ManageIntegrations:
    return ManageIntegrations(
        integrations=repo or FakeRepo(),
        registry=ProviderRegistry(encryptor=Encryptor(_KEY)),
        encryptor=Encryptor(_KEY),
        uow=FakeUnitOfWork(),
    )


async def test_secrets_are_encrypted_and_never_stored_in_plaintext():
    manager = _manager()
    created = await manager.create(
        project="GCM", env="prod", provider="aws", config=_AWS_CONFIG,
        secrets={"access_key_id": "AKIAEXAMPLE", "secret_access_key": "s3cret"},
        capabilities=(ALARMS,),
    )
    stored = created.encrypted_secrets
    assert set(stored) == {"access_key_id", "secret_access_key"}
    assert "AKIAEXAMPLE" not in str(stored) and "s3cret" not in str(stored)
    assert Encryptor(_KEY).decrypt(stored["access_key_id"]) == "AKIAEXAMPLE"


async def test_a_secret_the_provider_doesnt_declare_is_dropped():
    """Anything not on the provider's list would be stored and never read — a credential sitting
    in the database that nothing can use."""
    created = await _manager().create(
        project="GCM", env="prod", provider="newrelic", config={"account_id": "123"},
        secrets={"api_key": "nr-key", "totally_made_up": "x"}, capabilities=(ALARMS,),
    )
    assert set(created.encrypted_secrets) == {"api_key"}


async def test_missing_required_config_is_rejected_with_the_field_named():
    with pytest.raises(InvalidIntegrationError, match="account_id"):
        await _manager().create(
            project="GCM", env="prod", provider="newrelic", config={},
            secrets={"api_key": "k"}, capabilities=(ALARMS,),
        )


async def test_blank_required_config_counts_as_missing():
    with pytest.raises(InvalidIntegrationError, match="region"):
        await _manager().create(
            project="GCM", env="prod", provider="aws",
            config={"region": "   ", "auth_type": "sso"}, secrets={}, capabilities=(ALARMS,),
        )


async def test_a_capability_the_provider_cannot_do_is_rejected():
    with pytest.raises(InvalidIntegrationError, match="cost"):
        await _manager().create(
            project="GCM", env="prod", provider="newrelic", config={"account_id": "123"},
            secrets={"api_key": "k"}, capabilities=(ALARMS, COST),
        )


async def test_no_capabilities_given_defaults_to_everything_the_provider_supports():
    """An integration with no capability would be stored, used by nothing, and look broken for
    reasons the UI could never explain."""
    created = await _manager().create(
        project="GCM", env="prod", provider="aws", config=_AWS_CONFIG, secrets={}, capabilities=(),
    )
    assert set(created.capabilities) == {ALARMS, LOGS, COST}


async def test_update_keeps_a_secret_the_caller_did_not_resend():
    """Editing a region must not require re-typing a PAT nobody has to hand."""
    repo = FakeRepo()
    manager = _manager(repo)
    created = await manager.create(
        project="EVP", env="all", provider="azure_devops",
        config={"organization": "org", "ado_project": "proj", "work_item_type": "Bug"},
        secrets={"pat": "the-pat"}, capabilities=(TICKETS,),
    )
    original = created.encrypted_secrets["pat"]

    updated = await manager.update(
        created.id, project="EVP", env="all",
        config={"organization": "org2", "ado_project": "proj", "work_item_type": "Bug"},
        secrets={}, capabilities=(TICKETS,),
    )

    assert updated.config["organization"] == "org2"
    assert updated.encrypted_secrets["pat"] == original


async def test_update_replaces_a_secret_that_is_resent():
    repo = FakeRepo()
    manager = _manager(repo)
    created = await manager.create(
        project="GCM", env="prod", provider="newrelic", config={"account_id": "1"},
        secrets={"api_key": "old"}, capabilities=(ALARMS,),
    )
    updated = await manager.update(
        created.id, project="GCM", env="prod", config={"account_id": "1"},
        secrets={"api_key": "new"}, capabilities=(ALARMS,),
    )
    assert Encryptor(_KEY).decrypt(updated.encrypted_secrets["api_key"]) == "new"


async def test_update_cannot_change_the_provider():
    """Config and secrets are shaped for the provider that was chosen; swapping it would leave
    both meaningless. The `provider` field in the request body is simply ignored."""
    repo = FakeRepo()
    manager = _manager(repo)
    created = await manager.create(
        project="GCM", env="prod", provider="newrelic", config={"account_id": "1"},
        secrets={"api_key": "k"}, capabilities=(ALARMS,),
    )
    updated = await manager.update(
        created.id, project="GCM", env="prod", config={"account_id": "2"},
        secrets={}, capabilities=(ALARMS,),
    )
    assert updated.provider == "newrelic"


async def test_testing_a_tracker_without_a_stored_token_says_so_in_words():
    """The Settings button used to answer "No test available for azure_devops yet" no matter what
    was configured — the probe only knew how to fetch alarms, so a tracker's PAT was never
    exercised at all. Now it is, and the failure names what's missing."""
    repo = FakeRepo(
        [
            Integration(
                id=(iid := uuid.uuid4()), project="EVP", env="all", provider="azure_devops",
                config={"organization": "o", "ado_project": "p"}, capabilities=(TICKETS,),
            )
        ]
    )
    ok, error = await _manager(repo).test(iid)
    assert ok is False
    assert "access token" in error


async def test_testing_a_tracker_calls_the_provider_rather_than_skipping_it():
    """Guards the regression directly: a tickets-only provider must reach a real probe."""
    calls = []

    class RecordingSink:
        async def verify(self, integration):
            calls.append(integration.id)

    manager = _manager(
        FakeRepo(
            [
                Integration(
                    id=(iid := uuid.uuid4()), project="EVP", env="all", provider="azure_devops",
                    config={"organization": "o", "ado_project": "p"},
                    encrypted_secrets={"pat": "x"}, capabilities=(TICKETS,),
                )
            ]
        )
    )
    manager.registry.ticket_sink = lambda provider: RecordingSink()
    assert await manager.test(iid) == (True, None)
    assert calls == [iid]
