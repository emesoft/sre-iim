"""App-settings HTTP controller: a generic encrypted-secret endpoint. First (only) use today is
the Claude Code headless OAuth token for the local-demo `claude_cli` LLM provider — see
`app/infrastructure/llm/claude_cli.py`. The response never round-trips the secret value itself,
same principle as the cloud-connections endpoints for AWS access keys.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.infrastructure.db.repositories import SqlAlchemyAppSettingsRepository
from app.infrastructure.security.encryptor import Encryptor
from app.infrastructure.security.keys import CLAUDE_CLI_TOKEN_KEY
from app.interface.http.deps import get_app_settings_repository, get_encryptor, get_unit_of_work
from app.interface.http.dto.request import SetTokenRequest
from app.interface.http.dto.response import SettingStatus

router = APIRouter(prefix="/api/settings", tags=["settings"])


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
