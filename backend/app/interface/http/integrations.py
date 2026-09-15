"""Integration HTTP controller: one CRUD surface for every provider.

Replaces the separate `/api/cloud-connections` and `/api/ado-connections` controllers, which were
the same endpoints written twice. Validation of what a given provider needs lives in the use case,
against the registry's declaration — this layer only translates.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.application.integrations.manage import InvalidIntegrationError, ManageIntegrations
from app.application.integrations.poll_alarms import PollAlarmsJob
from app.application.integrations.sso_connect import (
    SsoConnections,
    integration_config,
    integration_secrets,
    parse_accounts,
    visible_roles,
)
from app.infrastructure.cloud.aws_sso import SsoAuthorizationExpired, SsoAuthorizationPending
from app.domain.integrations.entities import ALARMS
from app.domain.integrations.ports import IntegrationRepository
from app.domain.projects.errors import UnknownProjectError
from app.infrastructure.config import Settings, get_settings
from app.infrastructure.integrations.registry import ProviderRegistry
from app.interface.http.deps import (
    get_sso_connections,
    get_integration_repository,
    get_manage_integrations,
    get_poll_alarms_job,
    get_provider_registry,
    require_role,
)
from app.interface.http.dto import mappers
from app.interface.http.dto.request import (
    IntegrationCreateRequest,
    SsoBeginRequest,
    SsoFinishRequest,
)
from app.interface.http.dto.response import (
    SsoBeginOut,
    SsoPollOut,
    SsoRoleOut,
    IntegrationOut,
    PollResult,
    PollScheduleOut,
    ProviderOut,
    TestConnectionResult,
)

router = APIRouter(
    prefix="/api/integrations",
    tags=["integrations"],
    dependencies=[Depends(require_role("admin"))],
)

providers_router = APIRouter(
    prefix="/api/providers",
    tags=["integrations"],
    dependencies=[Depends(require_role("admin"))],
)


@providers_router.get("", response_model=list[ProviderOut])
async def list_providers(
    registry: ProviderRegistry = Depends(get_provider_registry),
) -> list[ProviderOut]:
    """The provider catalog: what each one can do and which fields it needs. The Settings form
    builds itself from this, so adding a provider needs no frontend change."""
    return [mappers.provider_out(registry.spec(p), p) for p in registry.providers]


async def _out(
    integration, repo: IntegrationRepository
) -> IntegrationOut:
    return mappers.integration_out(integration, await repo.health_for(integration.id))


@router.get("", response_model=list[IntegrationOut])
async def list_integrations(
    manager: ManageIntegrations = Depends(get_manage_integrations),
    repo: IntegrationRepository = Depends(get_integration_repository),
) -> list[IntegrationOut]:
    return [await _out(i, repo) for i in await manager.list()]


# Declared before `/{integration_id}` routes: FastAPI matches in definition order, so "aws-sso"
# would otherwise be parsed as an integration id and 422 on the UUID conversion.
@router.post("/aws-sso/begin", response_model=SsoBeginOut)
async def begin_aws_sso(
    body: SsoBeginRequest,
    connections: SsoConnections = Depends(get_sso_connections),
) -> SsoBeginOut:
    """Start an AWS SSO sign-in from the browser.

    Needs nothing on the machine running this — no `~/.aws`, no CLI, no pre-existing profile —
    which is the whole point: a fresh deployment can connect an AWS account through the web UI.
    """
    try:
        handle, entry = await connections.begin(
            body.start_url, body.sso_region, parse_accounts(body.only_accounts)
        )
    except Exception as exc:  # noqa: BLE001 - a bad start URL or region is the operator's to fix
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not start AWS SSO sign-in: {exc}",
        ) from exc
    return SsoBeginOut(
        handle=handle,
        verification_uri_complete=entry.auth.verification_uri_complete,
        user_code=entry.auth.user_code,
        interval_seconds=entry.auth.interval_seconds,
        expires_in_seconds=entry.auth.expires_in_seconds,
    )


@router.post("/aws-sso/{handle}/poll", response_model=SsoPollOut)
async def poll_aws_sso(
    handle: str,
    connections: SsoConnections = Depends(get_sso_connections),
) -> SsoPollOut:
    """Has the operator approved yet? Returns the account/role pairs once they have.

    "Not yet" is a normal answer with a 200, not an error: the UI calls this on a timer, and a
    stream of 4xx responses for the expected case makes a real failure impossible to spot.
    """
    entry = connections.get(handle)
    if entry is None:
        return SsoPollOut(status="expired")
    if entry.token is None:
        try:
            entry.token = await connections.client_for(entry).poll(entry.auth)
        except SsoAuthorizationPending:
            return SsoPollOut(status="pending")
        except SsoAuthorizationExpired:
            connections.finish(handle)
            return SsoPollOut(status="expired")
    roles = visible_roles(
        entry, await connections.client_for(entry).roles(entry.token.access_token)
    )
    return SsoPollOut(
        status="ready",
        roles=[
            SsoRoleOut(
                account_id=r.account_id, account_name=r.account_name, role_name=r.role_name
            )
            for r in roles
        ],
    )


@router.post(
    "/aws-sso/{handle}/finish", response_model=IntegrationOut, status_code=status.HTTP_201_CREATED
)
async def finish_aws_sso(
    handle: str,
    body: SsoFinishRequest,
    connections: SsoConnections = Depends(get_sso_connections),
    manager: ManageIntegrations = Depends(get_manage_integrations),
    repo: IntegrationRepository = Depends(get_integration_repository),
) -> IntegrationOut:
    """Store the finished sign-in as an integration, pinned to the chosen account and role."""
    entry = connections.get(handle)
    if entry is None or entry.token is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="That sign-in has expired — start it again.",
        )
    config = integration_config(entry, body.account_id, body.role_name, body.region)
    secrets = integration_secrets(entry)
    try:
        if body.integration_id is not None:
            # Switching an existing connection to SSO rather than adding another one. Two AWS
            # integrations for the same project would leave `for_provider` picking whichever was
            # created first — quite possibly the one that no longer works.
            created = await manager.update(
                body.integration_id,
                project=body.project,
                env=body.env,
                config=config,
                secrets=secrets,
                capabilities=tuple(body.capabilities),
                display_name=body.display_name,
            )
        else:
            created = await manager.create(
                project=body.project,
                env=body.env,
                provider="aws",
                config=config,
                secrets=secrets,
                capabilities=tuple(body.capabilities),
                display_name=body.display_name,
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except UnknownProjectError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except InvalidIntegrationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    connections.finish(handle)
    return mappers.integration_out(created, await repo.health_for(created.id))


@router.post("", response_model=IntegrationOut, status_code=status.HTTP_201_CREATED)
async def create_integration(
    body: IntegrationCreateRequest,
    manager: ManageIntegrations = Depends(get_manage_integrations),
    repo: IntegrationRepository = Depends(get_integration_repository),
) -> IntegrationOut:
    try:
        integration = await manager.create(
            project=body.project,
            env=body.env,
            provider=body.provider,
            config=body.config,
            secrets=body.secrets,
            capabilities=tuple(body.capabilities),
            display_name=body.display_name,
        )
    except (InvalidIntegrationError, UnknownProjectError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return await _out(integration, repo)


@router.patch("/{integration_id}", response_model=IntegrationOut)
async def update_integration(
    integration_id: uuid.UUID,
    body: IntegrationCreateRequest,
    manager: ManageIntegrations = Depends(get_manage_integrations),
    repo: IntegrationRepository = Depends(get_integration_repository),
) -> IntegrationOut:
    try:
        integration = await manager.update(
            integration_id,
            project=body.project,
            env=body.env,
            config=body.config,
            secrets=body.secrets,
            capabilities=tuple(body.capabilities),
            display_name=body.display_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (InvalidIntegrationError, UnknownProjectError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return await _out(integration, repo)


@router.delete("/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_integration(
    integration_id: uuid.UUID,
    manager: ManageIntegrations = Depends(get_manage_integrations),
) -> None:
    await manager.delete(integration_id)


@router.post("/{integration_id}/pause", response_model=IntegrationOut)
async def pause_integration(
    integration_id: uuid.UUID,
    manager: ManageIntegrations = Depends(get_manage_integrations),
    repo: IntegrationRepository = Depends(get_integration_repository),
) -> IntegrationOut:
    """Leaves the scheduled sweep alone but keeps the credentials — e.g. while an expired token is
    being fixed, so it stops recording an error every tick. Manual Refresh/Test still work."""
    return await _set_enabled(integration_id, False, manager, repo)


@router.post("/{integration_id}/resume", response_model=IntegrationOut)
async def resume_integration(
    integration_id: uuid.UUID,
    manager: ManageIntegrations = Depends(get_manage_integrations),
    repo: IntegrationRepository = Depends(get_integration_repository),
) -> IntegrationOut:
    return await _set_enabled(integration_id, True, manager, repo)


async def _set_enabled(
    integration_id: uuid.UUID,
    enabled: bool,
    manager: ManageIntegrations,
    repo: IntegrationRepository,
) -> IntegrationOut:
    try:
        integration = await manager.set_enabled(integration_id, enabled)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return await _out(integration, repo)


@router.post("/{integration_id}/test", response_model=TestConnectionResult)
async def test_integration(
    integration_id: uuid.UUID,
    manager: ManageIntegrations = Depends(get_manage_integrations),
) -> TestConnectionResult:
    try:
        ok, error = await manager.test(integration_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return TestConnectionResult(ok=ok, error=error)


# Declared before `/{integration_id}/...` would shadow it — FastAPI matches in definition order.
@router.post("/poll", response_model=PollResult)
async def poll_all(job: PollAlarmsJob = Depends(get_poll_alarms_job)) -> PollResult:
    outcomes = await job.run()
    return PollResult(
        polled=len(outcomes),
        alarm_count=sum(o.alarm_count for o in outcomes),
        errors=sum(1 for o in outcomes if o.status == "error"),
    )


@router.get("/poll-schedule", response_model=PollScheduleOut)
async def poll_schedule(
    request: Request, settings: Settings = Depends(get_settings)
) -> PollScheduleOut:
    """The background poll is one global APScheduler job (see main.py's `lifespan`), not a
    per-integration timer — `next_run_at` is that job's own next-fire time, read off the scheduler
    stashed on `app.state` at startup. `None` if the scheduler isn't running (e.g. under the test
    client, which doesn't invoke the app's lifespan by default)."""
    scheduler = getattr(request.app.state, "scheduler", None)
    job = scheduler.get_job("poll_cloudwatch_alarms") if scheduler is not None else None
    return PollScheduleOut(
        interval_minutes=settings.alarm_poll_interval_minutes,
        next_run_at=job.next_run_time if job is not None else None,
    )


@router.post("/{integration_id}/poll", response_model=PollResult)
async def poll_one(
    integration_id: uuid.UUID,
    job: PollAlarmsJob = Depends(get_poll_alarms_job),
) -> PollResult:
    """Per-card "Refresh" — polls just this integration, paused or not."""
    integration = await job.integrations.get(integration_id)
    if integration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="integration not found")
    if not integration.supports(ALARMS):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{integration.provider} has no alarms to poll",
        )
    outcome = (await job.run(connection_id=integration_id))[0]
    return PollResult(
        polled=1, alarm_count=outcome.alarm_count, errors=1 if outcome.status == "error" else 0
    )
