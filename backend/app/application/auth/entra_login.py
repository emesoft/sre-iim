"""EntraLogin: sign in with a Microsoft Entra ID token, issue this application's own JWT.

Entra answers "who is this person"; this application still answers "what may they do". A verified
token is exchanged for the same access token the password login issues, so every authorization
decision downstream (require_role, the RBAC in deps.py) is unchanged and unaware of how the user
arrived.

First sign-in provisions the account at the **lowest** role. Anyone in the company directory can
authenticate — that's the point of single sign-on — so granting anything above read-only on the
strength of "Entra recognised them" would hand the whole tenant write access to incidents. An
admin promotes from the Users page.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.users.entities import User
from app.domain.users.ports import UserRepository
from app.domain.shared import UnitOfWork
from app.infrastructure.security.entra import EntraIdentity, verify_id_token
from app.infrastructure.security.jwt import create_access_token

PROVIDER = "entra"


@dataclass
class EntraLogin:
    users: UserRepository
    uow: UnitOfWork
    jwt_secret: str
    jwt_ttl_seconds: int
    tenant_id: str
    client_id: str

    @property
    def configured(self) -> bool:
        return bool(self.tenant_id and self.client_id)

    async def execute(self, id_token: str) -> tuple[User, str]:
        identity = await verify_id_token(
            id_token, tenant_id=self.tenant_id, client_id=self.client_id
        )
        user = await self._find_or_create(identity)
        return user, create_access_token(user, self.jwt_secret, self.jwt_ttl_seconds)

    async def _find_or_create(self, identity: EntraIdentity) -> User:
        existing = await self.users.get_by_external_id(PROVIDER, identity.external_id)
        if existing is not None:
            # Deliberately doesn't touch the group: an admin's assignment must survive the
            # user's next sign-in.
            return existing
        user = await self.users.add(
            User(
                username=await self._available_username(identity.username),
                password_hash=None,  # the credential lives in Entra, not here
                # No group: a first sign-in lands in Guest and can read nothing. Everyone in the
                # company directory can authenticate — that's what SSO means — so provisioning
                # with any actual permission would hand the whole tenant access.
                email=identity.email,
                auth_provider=PROVIDER,
                external_id=identity.external_id,
            )
        )
        await self.uow.commit()
        return user

    async def _available_username(self, preferred: str) -> str:
        """Usernames are unique in this system but Entra's `preferred_username` isn't guaranteed
        not to collide with an existing local account — so a clash gets a suffix rather than
        failing the login or, worse, attaching this Entra identity to someone else's account."""
        if await self.users.get_by_username(preferred) is None:
            return preferred
        for suffix in range(2, 100):
            candidate = f"{preferred}+{suffix}"
            if await self.users.get_by_username(candidate) is None:
                return candidate
        raise RuntimeError(f"could not find an available username for {preferred!r}")
