"""App-settings HTTP controller: a generic encrypted-secret endpoint. First (only) use today is
the Claude Code headless OAuth token for the local-demo `claude_cli` LLM provider — see
`app/infrastructure/llm/claude_cli.py`. The response never round-trips the secret value itself,
same principle as the cloud-connections endpoints for AWS access keys.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.domain.incidents.ports import IncidentRepository
from app.infrastructure.config import Settings, get_settings
from app.infrastructure.db.repositories import SqlAlchemyAppSettingsRepository
from app.infrastructure.llm.claude_cli import verify_claude_cli_token
from app.infrastructure.security.encryptor import Encryptor
from app.infrastructure.security.keys import CLAUDE_CLI_TOKEN_KEY
from app.interface.http.deps import (
    get_app_settings_repository,
    get_encryptor,
    get_incident_repository,
    get_unit_of_work,
    require_admin,
)
from app.interface.http.dto.request import SetTokenRequest
from app.interface.http.dto.response import (
    LlmUsageOut,
    SettingStatus,
    TestConnectionResult,
    UsageByModelOut,
)

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_admin)])


@router.get("/claude-token", response_model=SettingStatus)
async def get_claude_token_status(
    settings: SqlAlchemyAppSettingsRepository = Depends(get_app_settings_repository),
) -> SettingStatus:
    """Whether a Claude Code headless token is configured — never the token itself."""
    value = await settings.get(CLAUDE_CLI_TOKEN_KEY)
    return SettingStatus(is_set=value is not None)


@router.put("/claude-token", status_code=status.HTTP_204_NO_CONTENT)
async def set_claude_token(
    body: SetTokenRequest,
    settings: SqlAlchemyAppSettingsRepository = Depends(get_app_settings_repository),
    encryptor: Encryptor = Depends(get_encryptor),
    uow=Depends(get_unit_of_work),
) -> None:
    """Encrypt and store the Claude Code headless OAuth token (from `claude setup-token`)."""
    await settings.set(CLAUDE_CLI_TOKEN_KEY, encryptor.encrypt(body.token))
    await uow.commit()


@router.post("/claude-token/test", response_model=TestConnectionResult)
async def test_claude_token(settings: Settings = Depends(get_settings)) -> TestConnectionResult:
    """Make a real, minimal `claude -p` call with the stored token — a saved token can still be
    expired/revoked, so "is configured" alone doesn't mean "still works"."""
    ok, error = await verify_claude_cli_token(settings)
    return TestConnectionResult(ok=ok, error=error)


@router.get("/llm-usage", response_model=LlmUsageOut)
async def get_llm_usage(
    incidents: IncidentRepository = Depends(get_incident_repository),
) -> LlmUsageOut:
    """Real LLM token spend, grouped by model — cache hits and providers that don't report usage
    are excluded (see UsageByModel docstring)."""
    by_model = await incidents.usage_by_model()
    return LlmUsageOut(
        total_input_tokens=sum(u.input_tokens for u in by_model),
        total_output_tokens=sum(u.output_tokens for u in by_model),
        by_model=[
            UsageByModelOut(
                model_id=u.model_id,
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
                analyses_count=u.analyses_count,
            )
            for u in by_model
        ],
    )
