"""Unit tests for ProviderRegistry — the declaration of which provider can do what.

Replaces test_dispatching_alarm_fetcher.py: routing used to be an if/else on `connection.cloud`,
and is now a lookup in one table that the poller and (later) the Settings UI both read. No I/O —
building an adapter never touches the network.
"""

import pytest

from app.domain.integrations.entities import ALARMS, COST, LOGS, TICKETS
from app.domain.integrations.ports import UnsupportedCapabilityError
from app.infrastructure.cloud.cloudwatch_alarms import CloudWatchAlarmFetcher
from app.infrastructure.cloud.newrelic_issues import NewRelicIssueFetcher
from app.infrastructure.integrations.registry import AWS, AZURE_DEVOPS, NEWRELIC, ProviderRegistry
from app.infrastructure.security.encryptor import Encryptor

_KEY = "zH8yV2m3sVW6tG5v9pQwQflR4z1sT8y3lU9wA0b3iF4="


def _registry() -> ProviderRegistry:
    return ProviderRegistry(encryptor=Encryptor(_KEY))


def test_alarm_sources_are_built_per_provider():
    registry = _registry()
    assert isinstance(registry.alarm_source(AWS), CloudWatchAlarmFetcher)
    assert isinstance(registry.alarm_source(NEWRELIC), NewRelicIssueFetcher)


def test_one_aws_credential_serves_several_capabilities():
    """The reason this refactor exists: the previous model could only express one job per
    connection, but an AWS credential polls alarms, reads logs and (next) reads cost."""
    assert _registry().capabilities(AWS) == frozenset({ALARMS, LOGS, COST})


def test_a_provider_only_advertises_what_it_can_actually_do():
    registry = _registry()
    assert registry.capabilities(NEWRELIC) == frozenset({ALARMS})
    assert registry.capabilities(AZURE_DEVOPS) == frozenset({TICKETS})
    assert registry.supports(NEWRELIC, ALARMS)
    assert not registry.supports(NEWRELIC, COST)
    assert not registry.supports(AZURE_DEVOPS, ALARMS)


def test_asking_a_provider_for_a_capability_it_lacks_raises():
    """Loudly, rather than returning nothing: a caller that reached here has a bug, and silently
    finding "no alarms" in Azure DevOps would look like a quiet, healthy poll forever."""
    with pytest.raises(UnsupportedCapabilityError, match="cannot supply alarms"):
        _registry().alarm_source(AZURE_DEVOPS)


def test_an_advertised_but_unimplemented_capability_still_raises_when_asked_for():
    """AWS advertises `cost` so the Settings UI can offer the toggle, but no adapter exists yet."""
    registry = _registry()
    assert registry.supports(AWS, COST)
    # There is no cost accessor at all yet — the capability is declared, not wired. When one is
    # added, this test should become the "returns an adapter" case.
    assert not hasattr(registry, "cost_source")


def test_unknown_provider_raises():
    with pytest.raises(UnsupportedCapabilityError, match="unknown provider"):
        _registry().spec("gcp")


def test_every_provider_declares_the_config_and_secrets_its_adapter_reads():
    """The Settings form builds itself from these, so a provider that declares nothing would
    render an empty form and store an unusable integration."""
    registry = _registry()
    for provider in registry.providers:
        spec = registry.spec(provider)
        assert spec.label
        assert spec.capabilities
        assert spec.required_config, f"{provider} declares no config keys"
