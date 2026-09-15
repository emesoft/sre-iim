"""App-settings HTTP controller: a generic encrypted-secret endpoint. First (only) use today is
the Claude Code headless OAuth token for the local-demo `claude_cli` LLM provider — see
`app/infrastructure/llm/claude_cli.py`. The response never round-trips the secret value itself,
same principle as the cloud-connections endpoints for AWS access keys.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.incidents.ports import IncidentRepository
from app.infrastructure.config import Settings, get_settings
from app.infrastructure.db.repositories import SqlAlchemyAppSettingsRepository
from app.infrastructure.llm import factory as llm_factory
from app.infrastructure.llm.catalog import CATALOG, CLAUDE_CLI
from app.infrastructure.llm.claude_cli import verify_claude_cli_token
from app.infrastructure.security.encryptor import Encryptor
from app.infrastructure.security.keys import CLAUDE_CLI_TOKEN_KEY
from app.interface.http.deps import (
    get_app_settings_repository,
    get_encryptor,
    get_incident_repository,
    get_session,
    get_unit_of_work,
    require_role,
)
from app.interface.http.dto.request import (
    SaveLlmProfileRequest,
    SetDefaultProfileRequest,
    SetProjectProfileRequest,
    SetTokenRequest,
)
from app.interface.http.dto.response import (
    LlmFieldOut,
    LlmProfileIdOut,
    LlmProfileOut,
    LlmProviderOut,
    LlmSetupOut,
    LlmUsageOut,
    SettingStatus,
    TestConnectionResult,
    UsageByModelOut,
)

router = APIRouter(
    prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_role("admin"))]
)


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


def _provider_out(provider_spec) -> LlmProviderOut:
    return LlmProviderOut(
        key=provider_spec.key,
        label=provider_spec.label,
        notes=provider_spec.notes,
        fields=[
            LlmFieldOut(
                name=f.name, label=f.label, secret=f.secret, required=f.required,
                placeholder=f.placeholder, help=f.help, source=f.source,
                options=list(f.options),
            )
            for f in provider_spec.fields
        ],
        defaults=dict(provider_spec.defaults),
    )


@router.get("/llm", response_model=LlmSetupOut)
async def get_llm_setup(
    session: AsyncSession = Depends(get_session),
    encryptor: Encryptor = Depends(get_encryptor),
) -> LlmSetupOut:
    """Every profile, which one is the default, which projects override it — plus the catalog the
    form renders itself from. Never returns a secret, only which ones exist."""
    setup = await llm_factory.load_setup(session, encryptor)
    return LlmSetupOut(
        profiles=[
            LlmProfileOut(
                id=p.id,
                name=p.name,
                provider=p.provider,
                config=p.config,
                secret_names=await llm_factory.stored_secret_names(session, encryptor, p),
                last_test_ok=p.last_test_ok,
                last_test_error=p.last_test_error,
            )
            for p in setup.profiles
        ],
        default_profile_id=setup.default_profile_id,
        by_project=setup.by_project,
        providers=[_provider_out(sp) for sp in CATALOG.values()],
    )


@router.post("/llm/profiles", response_model=LlmProfileIdOut, status_code=status.HTTP_201_CREATED)
async def create_llm_profile(
    body: SaveLlmProfileRequest,
    session: AsyncSession = Depends(get_session),
    encryptor: Encryptor = Depends(get_encryptor),
    uow=Depends(get_unit_of_work),
) -> LlmProfileIdOut:
    profile_id = await _save(session, encryptor, None, body)
    await uow.commit()
    return LlmProfileIdOut(id=profile_id)


@router.patch("/llm/profiles/{profile_id}", response_model=LlmProfileIdOut)
async def update_llm_profile(
    profile_id: str,
    body: SaveLlmProfileRequest,
    session: AsyncSession = Depends(get_session),
    encryptor: Encryptor = Depends(get_encryptor),
    uow=Depends(get_unit_of_work),
) -> LlmProfileIdOut:
    saved = await _save(session, encryptor, profile_id, body)
    await uow.commit()
    return LlmProfileIdOut(id=saved)


async def _save(session, encryptor, profile_id, body: SaveLlmProfileRequest) -> str:
    try:
        return await llm_factory.save_profile(
            session, encryptor, profile_id=profile_id, name=body.name,
            provider=body.provider, config=body.config, secrets=body.secrets,
        )
    except llm_factory.ProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.delete("/llm/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_profile(
    profile_id: str,
    session: AsyncSession = Depends(get_session),
    encryptor: Encryptor = Depends(get_encryptor),
    uow=Depends(get_unit_of_work),
) -> None:
    """Projects pointing at it fall back to the default rather than keeping a dangling pointer."""
    try:
        await llm_factory.delete_profile(session, encryptor, profile_id)
    except llm_factory.ProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except llm_factory.LastProfileError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await uow.commit()


@router.put("/llm/default", status_code=status.HTTP_204_NO_CONTENT)
async def set_default_llm_profile(
    body: SetDefaultProfileRequest,
    session: AsyncSession = Depends(get_session),
    encryptor: Encryptor = Depends(get_encryptor),
    uow=Depends(get_unit_of_work),
) -> None:
    try:
        await llm_factory.set_default(session, encryptor, body.profile_id)
    except llm_factory.ProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await uow.commit()


@router.put("/llm/projects/{project}", status_code=status.HTTP_204_NO_CONTENT)
async def set_project_llm_profile(
    project: str,
    body: SetProjectProfileRequest,
    session: AsyncSession = Depends(get_session),
    encryptor: Encryptor = Depends(get_encryptor),
    uow=Depends(get_unit_of_work),
) -> None:
    """Point one project at a profile, or send `null` to clear the override and follow the default."""
    try:
        await llm_factory.set_project_profile(session, encryptor, project, body.profile_id)
    except llm_factory.ProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await uow.commit()


@router.post("/llm/profiles/{profile_id}/test", response_model=TestConnectionResult)
async def test_llm_profile(
    profile_id: str,
    session: AsyncSession = Depends(get_session),
    encryptor: Encryptor = Depends(get_encryptor),
    settings: Settings = Depends(get_settings),
    uow=Depends(get_unit_of_work),
) -> TestConnectionResult:
    """One real, minimal call with what this profile has stored. A key can be well-formed, saved,
    and still rejected — revoked, wrong workspace, or no access to the model that was typed in."""
    setup = await llm_factory.load_setup(session, encryptor)
    profile = setup.get(profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such profile")
    if profile.provider == CLAUDE_CLI:
        ok, error = await verify_claude_cli_token(settings)
    else:
        try:
            resolved = await llm_factory.resolve_profile_credentials(session, encryptor, profile)
            await llm_factory.verify(resolved, settings)
            ok, error = True, None
        except Exception as exc:  # noqa: BLE001 - reported as a test result, not raised
            ok, error = False, str(exc) or type(exc).__name__
    await llm_factory.record_test_result(session, encryptor, profile_id, ok=ok, error=error)
    await uow.commit()
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
        total_cached_input_tokens=sum(u.cached_input_tokens for u in by_model),
        total_output_tokens=sum(u.output_tokens for u in by_model),
        by_model=[
            UsageByModelOut(
                model_id=u.model_id,
                llm_profile=u.llm_profile,
                cached_input_tokens=u.cached_input_tokens,
                unsplit_input_tokens=u.unsplit_input_tokens,
                source=u.source,
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
                analyses_count=u.analyses_count,
            )
            for u in by_model
        ],
    )
