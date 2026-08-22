"""Cloud-connection HTTP controller: CRUD for per-account AWS connections, a test-connection
action, and manual poll triggers (the Settings page "Refresh" button)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.application.cloud_connections.manage import ManageCloudConnections
from app.application.cloud_connections.poll_alarms import PollAlarmsJob
from app.interface.http.deps import get_manage_cloud_connections, get_poll_alarms_job
from app.interface.http.dto import mappers
from app.interface.http.dto.request import CloudConnectionCreateRequest
from app.interface.http.dto.response import CloudConnectionOut, PollResult, TestConnectionResult

router = APIRouter(prefix="/api/cloud-connections", tags=["cloud-connections"])

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
    connection = await manager.create(
        project=body.project, env=body.env, region=body.region, auth_type=body.auth_type,
        sso_profile_name=body.sso_profile_name, access_key_id=body.access_key_id,
        secret_access_key=body.secret_access_key,
    )
    return mappers.cloud_connection_out(connection)


@router.get("", response_model=list[CloudConnectionOut])
async def list_connections(
    manager: ManageCloudConnections = Depends(get_manage_cloud_connections),
) -> list[CloudConnectionOut]:
    connections = await manager.list()
    return [mappers.cloud_connection_out(c) for c in connections]


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
    connections = await job.connections.list()
    await job.run()
    return PollResult(polled=len(connections))
