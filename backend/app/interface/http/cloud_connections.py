"""Cloud-connection HTTP controller: CRUD for per-account AWS connections, a test-connection
action, and manual poll triggers (the Settings page "Refresh" button)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.application.cloud_connections.manage import ManageCloudConnections
from app.application.cloud_connections.poll_alarms import PollAlarmsJob
from app.domain.projects.errors import UnknownProjectError
from app.infrastructure.config import Settings, get_settings
from app.interface.http.deps import get_manage_cloud_connections, get_poll_alarms_job, require_admin
from app.interface.http.dto import mappers
from app.interface.http.dto.request import CloudConnectionCreateRequest
from app.interface.http.dto.response import (
    CloudConnectionOut,
    PollResult,
    PollScheduleOut,
    TestConnectionResult,
)

router = APIRouter(
    prefix="/api/cloud-connections", tags=["cloud-connections"], dependencies=[Depends(require_admin)]
)

_AUTH_TYPES = {"sso", "access_key"}


@router.post("", response_model=CloudConnectionOut, status_code=status.HTTP_201_CREATED)
async def create_connection(
    body: CloudConnectionCreateRequest,
    manager: ManageCloudConnections = Depends(get_manage_cloud_connections),
) -> CloudConnectionOut:
    if body.auth_type not in _AUTH_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"auth_type must be one of {sorted(_AUTH_TYPES)}",
        )
    if body.auth_type == "sso" and not body.sso_profile_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sso_profile_name is required when auth_type is 'sso'",
        )
    if body.auth_type == "access_key" and not (body.access_key_id and body.secret_access_key):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="access_key_id and secret_access_key are required when auth_type is 'access_key'",
        )
    try:
        connection = await manager.create(
            project=body.project, env=body.env, region=body.region, auth_type=body.auth_type,
            sso_profile_name=body.sso_profile_name, access_key_id=body.access_key_id,
            secret_access_key=body.secret_access_key,
        )
    except UnknownProjectError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return mappers.cloud_connection_out(connection)


@router.get("", response_model=list[CloudConnectionOut])
async def list_connections(
    manager: ManageCloudConnections = Depends(get_manage_cloud_connections),
) -> list[CloudConnectionOut]:
    connections = await manager.list()
    return [mappers.cloud_connection_out(c) for c in connections]


@router.patch("/{connection_id}", response_model=CloudConnectionOut)
async def update_connection(
    connection_id: uuid.UUID,
    body: CloudConnectionCreateRequest,
    manager: ManageCloudConnections = Depends(get_manage_cloud_connections),
) -> CloudConnectionOut:
    """Edit project/env/region/auth for an existing connection. For auth_type="access_key",
    leaving access_key_id/secret_access_key blank keeps the currently-stored credentials."""
    if body.auth_type not in _AUTH_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"auth_type must be one of {sorted(_AUTH_TYPES)}",
        )
    if body.auth_type == "sso" and not body.sso_profile_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sso_profile_name is required when auth_type is 'sso'",
        )
    try:
        connection = await manager.update(
            connection_id,
            project=body.project, env=body.env, region=body.region, auth_type=body.auth_type,
            sso_profile_name=body.sso_profile_name, access_key_id=body.access_key_id,
            secret_access_key=body.secret_access_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except UnknownProjectError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return mappers.cloud_connection_out(connection)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: uuid.UUID,
    manager: ManageCloudConnections = Depends(get_manage_cloud_connections),
) -> None:
    await manager.delete(connection_id)


@router.post("/{connection_id}/test", response_model=TestConnectionResult)
async def test_connection(
    connection_id: uuid.UUID,
    manager: ManageCloudConnections = Depends(get_manage_cloud_connections),
) -> TestConnectionResult:
    try:
        ok, error = await manager.test(connection_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return TestConnectionResult(ok=ok, error=error)


@router.post("/poll", response_model=PollResult)
async def poll_all(job: PollAlarmsJob = Depends(get_poll_alarms_job)) -> PollResult:
    outcomes = await job.run()
    return PollResult(
        polled=len(outcomes),
        alarm_count=sum(o.alarm_count for o in outcomes),
        errors=sum(1 for o in outcomes if o.status == "error"),
    )


@router.post("/{connection_id}/poll", response_model=PollResult)
async def poll_one(
    connection_id: uuid.UUID, job: PollAlarmsJob = Depends(get_poll_alarms_job)
) -> PollResult:
    """Per-row "Refresh" button on the Settings page — polls just this connection."""
    connection = await job.connections.get(connection_id)
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="connection not found")
    outcomes = await job.run(connection_id=connection_id)
    outcome = outcomes[0]
    return PollResult(
        polled=1, alarm_count=outcome.alarm_count, errors=1 if outcome.status == "error" else 0
    )


@router.get("/poll-schedule", response_model=PollScheduleOut)
async def poll_schedule(request: Request, settings: Settings = Depends(get_settings)) -> PollScheduleOut:
    """The background poll is one global APScheduler job (see main.py's `lifespan`), not a
    per-connection timer — `next_run_at` is that job's own next-fire time, read off the scheduler
    instance stashed on `app.state` at startup. `None` if the scheduler isn't running (e.g. under
    the test client, which doesn't invoke the app's lifespan by default)."""
    scheduler = getattr(request.app.state, "scheduler", None)
    job = scheduler.get_job("poll_cloudwatch_alarms") if scheduler is not None else None
    return PollScheduleOut(
        interval_minutes=settings.alarm_poll_interval_minutes,
        next_run_at=job.next_run_time if job is not None else None,
    )
