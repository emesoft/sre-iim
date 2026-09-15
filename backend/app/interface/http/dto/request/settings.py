"""App-settings request DTOs (the parse-first boundary)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SetTokenRequest(BaseModel):
    """`PUT /api/settings/{key}` body: the plaintext secret to encrypt and store."""

    token: str = Field(min_length=1)


class SaveLlmProfileRequest(BaseModel):
    """`POST /api/settings/llm/profiles` and `PATCH .../{id}`.

    `config` holds the non-secret fields; `secrets` is merged into what's stored, so omitting a key
    keeps the existing one and editing a model name never demands a credential again.
    """

    name: str = Field(min_length=1)
    provider: str
    config: dict[str, str] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)


class SetDefaultProfileRequest(BaseModel):
    profile_id: str


class SetProjectProfileRequest(BaseModel):
    """`null` clears the override so the project follows the default."""

    profile_id: str | None = None
