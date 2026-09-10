"""ADO-connection HTTP controller: CRUD for per-project Azure DevOps ticket destinations, plus a
test-connection action. Same shape as cloud_connections.py for AWS connections."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.application.ado_connections.manage import ManageAdoConnections
from app.domain.projects.errors import UnknownProjectError
from app.interface.http.deps import get_manage_ado_connections, require_role
from app.interface.http.dto import mappers
from app.interface.http.dto.request import AdoConnectionCreateRequest
from app.interface.http.dto.response import AdoConnectionOut, TestConnectionResult

router = APIRouter(
    prefix="/api/ado-connections",
    tags=["ado-connections"],
    dependencies=[Depends(require_role("admin"))],
)


@router.post("", response_model=AdoConnectionOut, status_code=status.HTTP_201_CREATED)
async def create_connection(
    body: AdoConnectionCreateRequest,
    manager: ManageAdoConnections = Depends(get_manage_ado_connections),
) -> AdoConnectionOut:
    if not body.pat:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="pat is required when creating a connection",
        )
    try:
        connection = await manager.create(
            project=body.project, org=body.org, ado_project=body.ado_project,
            pat=body.pat, work_item_type=body.work_item_type,
        )
    except UnknownProjectError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return mappers.ado_connection_out(connection)


@router.get("", response_model=list[AdoConnectionOut])
async def list_connections(
    manager: ManageAdoConnections = Depends(get_manage_ado_connections),
) -> list[AdoConnectionOut]:
    connections = await manager.list()
    return [mappers.ado_connection_out(c) for c in connections]


@router.patch("/{connection_id}", response_model=AdoConnectionOut)
async def update_connection(
    connection_id: uuid.UUID,
    body: AdoConnectionCreateRequest,
    manager: ManageAdoConnections = Depends(get_manage_ado_connections),
) -> AdoConnectionOut:
    """Edit project/org/ado_project for an existing connection. Leaving `pat` blank keeps the
    currently-stored PAT."""
    try:
        connection = await manager.update(
            connection_id,
            project=body.project, org=body.org, ado_project=body.ado_project,
            pat=body.pat, work_item_type=body.work_item_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except UnknownProjectError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return mappers.ado_connection_out(connection)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: uuid.UUID,
    manager: ManageAdoConnections = Depends(get_manage_ado_connections),
) -> None:
    await manager.delete(connection_id)


@router.post("/{connection_id}/test", response_model=TestConnectionResult)
async def test_connection(
    connection_id: uuid.UUID,
    manager: ManageAdoConnections = Depends(get_manage_ado_connections),
) -> TestConnectionResult:
    try:
        ok, error = await manager.test(connection_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return TestConnectionResult(ok=ok, error=error)
