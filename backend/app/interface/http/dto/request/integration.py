"""Integration request DTOs."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class IntegrationCreateRequest(BaseModel):
    """`POST /api/integrations`. On `PATCH`, `provider` is ignored — swapping providers would
    orphan config and secrets shaped for the old one, so it isn't editable.

    `secrets` carries plaintext values keyed by the provider's declared secret names; they're
    encrypted server-side and never sent back. Omitting one on update keeps the stored value.
    """

    project: str
    env: str = "all"
    provider: str
    display_name: str | None = None
    config: dict = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)


class SsoBeginRequest(BaseModel):
    """`POST /api/integrations/aws-sso/begin` — the two values from the AWS access portal. Both
    come from the portal page, not from any file on the machine running this."""

    start_url: str = Field(min_length=1)
    sso_region: str = Field(min_length=1)
    #: Optional account ids to list. Omitted, the sign-in returns every account it can reach —
    #: fine for one AWS account, uncomfortable for an organisation with fifty.
    only_accounts: str | None = None


class SsoFinishRequest(BaseModel):
    """`POST /api/integrations/aws-sso/{handle}/finish` — the account/role chosen from the list the
    sign-in returned, plus where the resulting integration belongs.

    `integration_id` switches an existing integration over to SSO instead of creating a second one.
    Without it, changing an access-key connection to SSO meant deleting and re-adding it, losing
    its health history for a change of credentials.
    """

    integration_id: uuid.UUID | None = None
    account_id: str = Field(min_length=1)
    role_name: str = Field(min_length=1)
    project: str = Field(min_length=1)
    env: str = Field(min_length=1)
    region: str = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    display_name: str | None = None
