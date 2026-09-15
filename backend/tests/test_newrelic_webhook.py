"""Unit tests for build_newrelic_context — pure function, no DB, no HTTP client.

Covers both the classic Alerts webhook shape (snake_case) and the newer workflow/NerdGraph shape
(camelCase), plus a near-empty payload to confirm it degrades gracefully instead of crashing.
"""

from app.application.webhooks.newrelic import build_newrelic_context


def test_classic_payload_builds_a_readable_alert_description():
    payload = {
        "policy_name": "GCM-SES-Alerts",
        "condition_name": "CRITICAL - SES Errors",
        "severity": "CRITICAL",
        "current_state": "open",
        "details": "Log query result is > 0.0 on 'CRITICAL - SES Errors'",
        "targets": [{"name": "prod-redemption-logs", "type": "Application"}],
        "runbook_url": "https://runbooks.example.com/ses-errors",
    }
    context = build_newrelic_context("gcm", payload)
    assert context["service"] == "gcm"
    assert "GCM-SES-Alerts" in context["alert"]
    assert "CRITICAL - SES Errors" in context["alert"]
    assert "CRITICAL" in context["alert"]
    assert "prod-redemption-logs" in context["alert"]
    assert context["runbook"] == "https://runbooks.example.com/ses-errors"


def test_camel_case_payload_is_also_understood():
    payload = {
        "policyName": "GCM-SES-Alerts",
        "conditionName": "CRITICAL - SES Errors",
        "priority": "CRITICAL",
        "state": "activated",
        "title": "Log query result is > 0.0",
        "impactedEntities": ["prod-redemption-logs"],
        "runbookUrl": "https://runbooks.example.com/ses-errors",
    }
    context = build_newrelic_context("gcm", payload)
    assert "GCM-SES-Alerts" in context["alert"]
    assert "prod-redemption-logs" in context["alert"]
    assert context["runbook"] == "https://runbooks.example.com/ses-errors"


def test_env_query_param_is_passed_through():
    context = build_newrelic_context("gcm", {}, env="prod")
    assert context["env"] == "prod"


def test_near_empty_payload_does_not_crash():
    context = build_newrelic_context("gcm", {})
    assert context["service"] == "gcm"
    assert "New Relic alert" in context["alert"]
    assert "env" not in context
    assert "runbook" not in context
