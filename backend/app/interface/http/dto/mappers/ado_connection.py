"""Mappers: ADO-connection domain entities -> response DTOs."""

from __future__ import annotations

from app.domain.ado_connections.entities import AdoConnection
from app.interface.http.dto.response.ado_connection import AdoConnectionOut


def ado_connection_out(connection: AdoConnection) -> AdoConnectionOut:
    return AdoConnectionOut(
        id=connection.id,
        project=connection.project,
        org=connection.org,
        ado_project=connection.ado_project,
        work_item_type=connection.work_item_type,
        created_at=connection.created_at,
    )
