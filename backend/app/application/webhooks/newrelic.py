"""Pure mapping from a New Relic alert-webhook payload to an incident context dict.

New Relic's classic Alerts webhook payload uses snake_case (`policy_name`, `condition_name`,
`current_state`, `targets: [{name, ...}]`); its newer workflow/NerdGraph-based webhooks use
camelCase (`policyName`, `conditionName`, `state`, `impactedEntities: [name, ...]`) — both shapes
are accepted here since New Relic accounts can be configured either way and this endpoint has no
control over which one a given account sends. Every field is read defensively with `.get()`; a
mostly-empty payload still produces a usable (if generic) incident rather than a 500.

Kept pure (no I/O, no FastAPI) so it's unit-testable without an HTTP client, same convention as
`poll_alarms.py`'s `_build_alert_context`.
"""

from __future__ import annotations


def _target_names(payload: dict) -> list[str]:
    targets = payload.get("targets") or payload.get("impactedEntities") or []
    if not isinstance(targets, list):
        return []
    names = []
    for t in targets:
        if isinstance(t, dict):
            name = t.get("name")
            if name:
                names.append(str(name))
        elif isinstance(t, str):
            names.append(t)
    return names


def build_newrelic_context(project: str, payload: dict, *, env: str | None = None) -> dict:
    """`project` comes from the webhook URL path (`/api/webhooks/newrelic/{project}`), the same
    role `connection.project` plays for CloudWatch-alarm incidents — it becomes `context["service"]`
    so the incident lands under the right project in the UI and RAG retrieval scopes to it."""
    policy = payload.get("policy_name") or payload.get("policyName")
    condition = payload.get("condition_name") or payload.get("conditionName")
    severity = payload.get("severity") or payload.get("priority")
    current_state = payload.get("current_state") or payload.get("state")
    details = payload.get("details") or payload.get("title")
    entities = _target_names(payload)

    headline_parts = []
    if severity:
        headline_parts.append(f"[{severity}]")
    headline_parts.append("New Relic alert")
    if condition:
        headline_parts.append(f"on '{condition}'")
    if policy:
        headline_parts.append(f"(policy '{policy}')")
    if current_state:
        headline_parts.append(f"— {current_state}")
    description = " ".join(headline_parts)
    if details:
        description += f": {details}"
    if entities:
        description += f" [entities: {', '.join(entities)}]"

    context: dict = {"service": project, "alert": description}
    if env:
        context["env"] = env
    runbook_url = payload.get("runbook_url") or payload.get("runbookUrl")
    if runbook_url:
        context["runbook"] = runbook_url
    return context
