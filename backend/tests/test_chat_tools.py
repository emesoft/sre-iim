"""Unit tests for the incident-chat tool surface — no CLI, no AWS.

The failure this guards against is silent by construction. A tool registered on the MCP server but
missing from `--allowedTools` isn't an error: it simply can't be called, and the model responds by
explaining it has no way to check and handing the user a list of `aws` commands to run and paste
back. That is exactly what shipped once, and nothing in the stack complained.
"""

import os

os.environ.setdefault("IIM_INCIDENT_SERVICE", "test-project")

from app.infrastructure.llm import mcp_log_tool  # noqa: E402
from app.infrastructure.llm.claude_cli import _ALLOWED_TOOLS, _chat_system_prompt  # noqa: E402

_TOOLS = ("fetch_logs", "describe_alarm", "metric_datapoints", "ecs_service_state")


def test_every_allowed_tool_exists_on_the_server():
    """A name in the allow-list that the server doesn't serve is a call that fails at runtime."""
    for name in (t.rsplit("__", 1)[-1] for t in _ALLOWED_TOOLS):
        assert hasattr(mcp_log_tool, name), name


def test_every_served_tool_is_allowed():
    """The direction that fails silently: served but not allowed means never called."""
    allowed = {t.rsplit("__", 1)[-1] for t in _ALLOWED_TOOLS}
    assert set(_TOOLS) == allowed


def test_the_tools_are_namespaced_to_this_server():
    assert all(t.startswith("mcp__iim-tools__") for t in _ALLOWED_TOOLS)


def test_the_prompt_tells_the_model_the_tools_exist():
    """Told only that it "has tools", the CLI answered an ECS question with `aws` commands for the
    reader to run. Naming them is what changed the behaviour."""
    prompt = _chat_system_prompt({"service": "EVP"})
    for name in _TOOLS:
        assert name in prompt, name


def test_the_prompt_forbids_handing_the_work_back():
    prompt = _chat_system_prompt({}).lower()
    assert "never ask the user to run an aws command" in prompt
    assert "never ask for aws credentials" in prompt


def test_the_incident_context_travels_with_the_prompt():
    assert "ecs-easyrx-prod" in _chat_system_prompt({"ecs": {"cluster": "ecs-easyrx-prod"}})
