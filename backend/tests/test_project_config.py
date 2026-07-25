"""Unit tests for per-project cloud config (decision: projects can each live on a different
cloud/account — GCM on AWS today, others may add Azure/GCP later). No network.
"""

import pytest

from app.infrastructure.config import Settings, get_project_config
from app.infrastructure.logs.cloudwatch_fetcher import CloudWatchLogFetcher
from app.infrastructure.logs.factory import build_log_fetcher


def _settings(**overrides) -> Settings:
    base = dict(aws_region="ap-southeast-1")
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_falls_back_to_global_aws_region_when_no_project_block(monkeypatch):
    monkeypatch.delenv("PROJECT_GCM_CLOUD", raising=False)
    monkeypatch.delenv("PROJECT_GCM_AWS_PROFILE", raising=False)
    monkeypatch.delenv("PROJECT_GCM_AWS_REGION", raising=False)
    project = get_project_config("GCM", _settings())
    assert project.cloud == "aws"
    assert project.aws_profile is None
    assert project.aws_region == "ap-southeast-1"


def test_reads_per_project_sso_profile_and_region(monkeypatch):
    monkeypatch.setenv("PROJECT_GCM_AWS_PROFILE", "GCM-Prod-ReadOnlyAccess")
    monkeypatch.setenv("PROJECT_GCM_AWS_REGION", "us-east-1")
    project = get_project_config("GCM", _settings())
    assert project.aws_profile == "GCM-Prod-ReadOnlyAccess"
    assert project.aws_region == "us-east-1"


def test_service_name_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("PROJECT_GCM_AWS_PROFILE", "GCM-Prod-ReadOnlyAccess")
    project = get_project_config("gcm", _settings())
    assert project.aws_profile == "GCM-Prod-ReadOnlyAccess"


def test_build_log_fetcher_returns_cloudwatch_adapter_for_aws(monkeypatch):
    monkeypatch.setenv("PROJECT_GCM_AWS_PROFILE", "GCM-Prod-ReadOnlyAccess")
    fetcher = build_log_fetcher("GCM", _settings())
    assert isinstance(fetcher, CloudWatchLogFetcher)
    assert fetcher._profile == "GCM-Prod-ReadOnlyAccess"
    assert fetcher._region == "ap-southeast-1"


def test_build_log_fetcher_raises_clearly_for_unwired_clouds(monkeypatch):
    monkeypatch.setenv("PROJECT_OTHERTEAM_CLOUD", "azure")
    with pytest.raises(NotImplementedError, match="azure"):
        build_log_fetcher("OtherTeam", _settings())
