"""ADO-connection request DTOs (the parse-first boundary)."""

from __future__ import annotations

from pydantic import BaseModel


class AdoConnectionCreateRequest(BaseModel):
    """`POST /api/ado-connections` body."""

    project: str
    org: str
    ado_project: str
    pat: str | None = None
    work_item_type: str = "Bug"
