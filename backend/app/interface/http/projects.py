"""Project-registry HTTP controller: CRUD for the shared project names that CloudConnection/
AdoConnection reference. Admin-gated, same shape as ado_connections.py."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.application.projects.manage import ManageProjects
from app.domain.projects.errors import ProjectInUseError, ProjectNameTakenError
from app.interface.http.deps import get_manage_projects, require_role
from app.interface.http.dto import mappers
from app.interface.http.dto.request import ProjectCreateRequest
from app.interface.http.dto.response import ProjectOut

router = APIRouter(
    prefix="/api/projects", tags=["projects"], dependencies=[Depends(require_role("admin"))]
)


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreateRequest,
    manager: ManageProjects = Depends(get_manage_projects),
) -> ProjectOut:
    if not body.name.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="name must not be blank"
        )
    try:
        project = await manager.create(body.name.strip())
    except ProjectNameTakenError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return mappers.project_out(project)


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    manager: ManageProjects = Depends(get_manage_projects),
) -> list[ProjectOut]:
    projects = await manager.list()
    return [mappers.project_out(p) for p in projects]


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID,
    manager: ManageProjects = Depends(get_manage_projects),
) -> None:
    try:
        await manager.delete(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ProjectInUseError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
