"""Mappers: cloud-connection domain entities -> response DTOs."""

from __future__ import annotations

from app.domain.cloud_connections.entities import CloudConnection
from app.interface.http.dto.response.cloud_connection import CloudConnectionOut


def cloud_connection_out(connection: CloudConnection) -> CloudConnectionOut:
    return CloudConnectionOut(
        id=connection.id,
        project=connection.project,
        env=connection.env,
        cloud=connection.cloud,
        region=connection.region,
        auth_type=connection.auth_type,
        sso_profile_name=connection.sso_profile_name,
        has_access_key=connection.encrypted_access_key_id is not None,
        last_poll_at=connection.last_poll_at,
        last_poll_status=connection.last_poll_status,
        last_poll_error=connection.last_poll_error,
        last_poll_alarm_count=connection.last_poll_alarm_count,
        created_at=connection.created_at,
    )
