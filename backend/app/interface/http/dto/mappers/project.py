"""Mappers: Project domain entity -> response DTO."""

from __future__ import annotations

from app.domain.projects.entities import Project
from app.interface.http.dto.response.project import ProjectOut


def project_out(project: Project) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        name=project.name,
        auto_analyze=project.auto_analyze,
        created_at=project.created_at,
    )
