"""App-settings request DTOs (the parse-first boundary)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SetTokenRequest(BaseModel):
    """`PUT /api/settings/{key}` body: the plaintext secret to encrypt and store."""

    token: str = Field(min_length=1)
