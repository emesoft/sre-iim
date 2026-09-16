"""Deploy history is gathered for every incident, not only the ones carrying an alarm name.

"Did something change?" is where nearly every analysis ends up, and it is answerable from the ECS
API without any CloudWatch alarm to start from. That matters most for New Relic incidents, which
have no alarm name at all and were therefore enriched with nothing — analysed on their notification
text alone, which is the "summary, not investigation" gap the enricher exists to close.

A *negative* answer carries the same weight as a positive one, so "no deployment in the window" has
to reach the prompt as a stated finding rather than as an absent section.

No AWS and no database: the credential resolver and the lookups are faked.
"""

from __future__ import annotations

import pytest

from app.domain.incidents.prompts import build_user_message
from app.infrastructure.cloud import aws_enricher
from app.infrastructure.cloud.aws_enricher import AwsContextEnricher
from app.domain.integrations.entities import Integration

ALARM = {
    "name": "ECS-CPUReservation-prod",
    "namespace": "AWS/ECS",
    "metric_name": "CPUReservation",
    "dimensions": {},
    "reason": "threshold crossed",
}
DEPLOYS = {
    "services_checked": 14,
    "since": "2026-09-16T04:00:00+00:00",
    "deploys": [
        {
            "service": "medusa-store",
            "cluster": "prod-ecs",
            "task_definition": "medusa-store:412",
            "started_at": "2026-09-16T05:41:00+00:00",
            "rollout": "COMPLETED",
        }
    ],
}
NO_DEPLOYS = {"services_checked": 14, "since": "2026-09-16T04:00:00+00:00", "deploys": []}


class _Lookups:
    def __init__(self, *, deploys=DEPLOYS, alarm=ALARM, deploys_raises=None):
        self._deploys, self._alarm, self._raises = deploys, alarm, deploys_raises

    def deployments(self, since, **_kw):
        if self._raises:
            raise self._raises
        return self._deploys

    def alarm(self, _name):
        return dict(self._alarm) if self._alarm else None

    def metrics(self, *_a, **_kw):
        return {}

    def ecs(self, *_a, **_kw):
        return {}


def _integration() -> Integration:
    return Integration(
        project="gcm",
        provider="aws",
        env="prod",
        config={"region": "us-east-2", "auth_type": "access_key"},
        encrypted_secrets={},
        capabilities=["logs"],
    )


@pytest.fixture
def enricher(monkeypatch):
    """Returns a factory: give it the fake lookups, get an enricher wired to them."""

    def _make(lookups: _Lookups, integration: Integration | None = _integration()):
        class _Repo:
            async def for_provider(self, _service, _provider):
                return integration

        class _Resolver:
            def __init__(self, _enc): ...

            def resolve(self, _integration):
                return object()

        monkeypatch.setattr(aws_enricher, "CredentialResolver", _Resolver)
        monkeypatch.setattr(aws_enricher, "AwsLookups", lambda **_kw: lookups)
        return AwsContextEnricher(integrations=_Repo(), encryptor=object())

    return _make


# --- collection --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deploy_history_is_collected_with_no_alarm_name(enricher) -> None:
    """The New Relic case. `enrich` used to return {} the moment no alarm name was found, so these
    incidents reached the model with nothing but their notification text."""
    out = await enricher(_Lookups()).enrich("gcm", {"alert": "CRITICAL - SES Errors"})

    assert out["deployments"] == DEPLOYS


@pytest.mark.asyncio
async def test_alarm_evidence_does_not_overwrite_the_deploy_history(enricher) -> None:
    """Both survive. The alarm branch assigns into the result rather than rebuilding it — written
    the other way round, the deploy history was silently dropped for exactly the incidents that
    had the most evidence."""
    out = await enricher(_Lookups()).enrich("gcm", {"alert": "alarm 'ECS-CPUReservation-prod'"})

    assert out["deployments"] == DEPLOYS
    assert out["alarm"]["name"] == "ECS-CPUReservation-prod"


@pytest.mark.asyncio
async def test_an_empty_deploy_list_is_still_reported(enricher) -> None:
    """"Nothing deployed" is a finding, so it has to be carried, not dropped as falsy."""
    out = await enricher(_Lookups(deploys=NO_DEPLOYS)).enrich("gcm", {"alert": "x"})

    assert out["deployments"]["deploys"] == []
    assert out["deployments"]["services_checked"] == 14


@pytest.mark.asyncio
async def test_a_failing_deploy_lookup_keeps_the_alarm_evidence(enricher) -> None:
    """One missing IAM permission must cost a section, never the whole enrichment."""
    broken = _Lookups(deploys_raises=RuntimeError("ecs:ListClusters denied"))

    out = await enricher(broken).enrich("gcm", {"alert": "alarm 'ECS-CPUReservation-prod'"})

    assert "deployments" not in out
    assert out["alarm"]["name"] == "ECS-CPUReservation-prod"


@pytest.mark.asyncio
async def test_a_project_with_no_aws_integration_is_left_alone(enricher) -> None:
    assert await enricher(_Lookups(), integration=None).enrich("gcm", {"alert": "x"}) == {}


# --- rendering ---------------------------------------------------------------------------------


def test_a_deployment_reaches_the_prompt_with_its_timestamp() -> None:
    rendered = build_user_message({"service": "gcm", "deployments": DEPLOYS})

    assert "[DEPLOYMENTS]" in rendered
    assert "medusa-store:412" in rendered
    assert "2026-09-16T05:41:00+00:00" in rendered


def test_no_deployments_renders_as_a_finding_not_as_silence() -> None:
    """The whole point of the negative answer: the model must be able to tell "we looked at 14
    services and none deployed" apart from "nobody checked"."""
    rendered = build_user_message({"service": "gcm", "deployments": NO_DEPLOYS})

    assert "[DEPLOYMENTS]" in rendered
    assert "14 service(s) checked" in rendered
    assert "ruled out" in rendered


def test_an_absent_section_stays_absent() -> None:
    assert "[DEPLOYMENTS]" not in build_user_message({"service": "gcm"})
