"""Unit tests for finding the AWS account behind a project — no DB.

`for_project(project, capability)` answers "give me an alarms source"; the AWS tools need "give me
this project's AWS account". Conflating them looked harmless while every project's alarms happened
to come from AWS, and quietly answers "no AWS here" for one whose alarms come from New Relic while
an AWS account is attached for logs.
"""

import uuid

from app.domain.integrations.entities import ALARMS, LOGS, Integration


class FakeRepo:
    """Mirrors the two lookups' SQL: first match ordered by creation."""

    def __init__(self, rows):
        self.rows = rows

    async def for_project(self, project, capability):
        return next(
            (r for r in self.rows if r.project == project and capability in r.capabilities), None
        )

    async def for_provider(self, project, provider):
        return next(
            (r for r in self.rows if r.project == project and r.provider == provider), None
        )


def _integration(provider, capabilities, project="gcm"):
    return Integration(
        id=uuid.uuid4(), project=project, env="prod", provider=provider,
        capabilities=tuple(capabilities),
    )


async def test_capability_lookup_can_return_the_wrong_vendor():
    """Documents why the second lookup exists: New Relic was created first and owns `alarms`, so
    the old "first alarms integration, then check it's AWS" path concluded there was no AWS."""
    repo = FakeRepo([_integration("newrelic", [ALARMS]), _integration("aws", [LOGS])])
    found = await repo.for_project("gcm", ALARMS)
    assert found.provider == "newrelic"


async def test_provider_lookup_finds_the_aws_account_regardless_of_capability():
    """The AWS tools need the account, not the alarm feed — an AWS integration carrying only
    `logs` still answers describe_alarm and ecs_service_state."""
    repo = FakeRepo([_integration("newrelic", [ALARMS]), _integration("aws", [LOGS])])
    found = await repo.for_provider("gcm", "aws")
    assert found is not None
    assert found.provider == "aws"


async def test_a_project_with_no_aws_account_still_reports_none():
    """gcm today: alarms from New Relic, tickets in Azure DevOps, no AWS at all. The tools must say
    so plainly rather than failing in a way that reads like the resource doesn't exist."""
    repo = FakeRepo([_integration("newrelic", [ALARMS]), _integration("azure_devops", ["tickets"])])
    assert await repo.for_provider("gcm", "aws") is None


async def test_the_lookup_is_scoped_to_one_project():
    """Another project's account is never a fallback, however plausible its name."""
    repo = FakeRepo([_integration("aws", [ALARMS], project="rxdevs")])
    assert await repo.for_provider("gcm", "aws") is None
    assert await repo.for_provider("rxdevs", "aws") is not None
