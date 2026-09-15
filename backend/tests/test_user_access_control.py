"""Unit tests for the two things that decide who can do what — no DB, no network.

Both are guards where the failure mode is silent and expensive: one locks every admin out of the
system permanently, the other quietly opens a second way in around single sign-on. Neither is
visible in normal use, so they need tests more than the happy paths do.
"""

import uuid

import pytest

from app.application.groups.manage import GroupNotFoundError, ManageGroups
from app.application.users.manage import ManageUsers
from app.domain.groups.entities import Group
from app.domain.users.entities import GUEST_ROLE, User
from app.domain.users.errors import LastAdminError, NotALocalAccountError
from app.domain.users.scope import ProjectScope


#: The groups the fakes resolve roles through. A user's role is their group's role — there is no
#: role stored on the account, which is the whole point of migration 0025.
ADMINS = Group(id=uuid.uuid4(), name="Admin", role="admin")
SRES = Group(id=uuid.uuid4(), name="SRE · gcm", role="sre", projects=("gcm",))
CONSULTANTS = Group(id=uuid.uuid4(), name="Consultant", role="consultant")


def _user(role="admin", provider="local", username="admin") -> User:
    group = {"admin": ADMINS, "sre": SRES, "consultant": CONSULTANTS}.get(role)
    return User(
        id=uuid.uuid4(), username=username, password_hash="hash" if provider == "local" else None,
        role=group.role if group else GUEST_ROLE, auth_provider=provider,
        group_id=group.id if group else None, group_name=group.name if group else None,
        projects=group.projects if group else (),
    )


class FakeUserRepo:
    def __init__(self, users):
        self.users = {u.id: u for u in users}
        self.deleted: list[uuid.UUID] = []
        self.password_updates: list[uuid.UUID] = []

    async def get_by_id(self, user_id):
        return self.users.get(user_id)

    async def list_all(self):
        return list(self.users.values())

    async def set_group(self, user_id, group_id):
        group = {g.id: g for g in (ADMINS, SRES, CONSULTANTS)}.get(group_id)
        user = self.users[user_id]
        user.group_id = group_id
        user.group_name = group.name if group else None
        user.role = group.role if group else GUEST_ROLE
        user.projects = group.projects if group else ()
        return user

    async def update_password_hash(self, user_id, password_hash):
        self.password_updates.append(user_id)
        self.users[user_id].password_hash = password_hash
        return self.users[user_id]

    async def delete(self, user_id):
        self.deleted.append(user_id)
        self.users.pop(user_id, None)


class FakeUnitOfWork:
    async def commit(self):
        pass

    async def rollback(self):
        pass


def _manager(*users) -> tuple[ManageUsers, FakeUserRepo]:
    repo = FakeUserRepo(users)
    return ManageUsers(users=repo, uow=FakeUnitOfWork()), repo


class FakeGroupRepo:
    def __init__(self, users: FakeUserRepo):
        self._users = users
        self.groups = {g.id: g for g in (ADMINS, SRES, CONSULTANTS)}
        self.deleted: list[uuid.UUID] = []

    async def get(self, group_id):
        return self.groups.get(group_id)

    async def list(self):
        return list(self.groups.values())

    async def update(self, group):
        self.groups[group.id] = group
        return group

    async def delete(self, group_id):
        self.deleted.append(group_id)
        self.groups.pop(group_id, None)

    async def count_members_with_role(self, role):
        return len([u for u in self._users.users.values() if u.role == role])


def _groups(*users) -> tuple[ManageGroups, FakeUserRepo, FakeGroupRepo]:
    user_repo = FakeUserRepo(users)
    group_repo = FakeGroupRepo(user_repo)
    return (
        ManageGroups(groups=group_repo, users=user_repo, uow=FakeUnitOfWork()),
        user_repo,
        group_repo,
    )


# --- the lock-yourself-out guard ---------------------------------------------------------------


async def test_the_last_admin_cannot_be_moved_out_of_the_admin_group():
    """The group dropdown on the Users page is one click; nothing in the app could undo this."""
    admin = _user()
    manager, users, _ = _groups(admin, _user(role="sre", username="sre"))
    with pytest.raises(LastAdminError, match="last admin"):
        await manager.assign(admin.id, SRES.id)
    assert users.users[admin.id].role == "admin"


async def test_the_last_admin_cannot_be_made_a_guest_either():
    """Assigning no group is the same removal by another name — it must hit the same guard."""
    admin = _user()
    manager, users, _ = _groups(admin)
    with pytest.raises(LastAdminError):
        await manager.assign(admin.id, None)
    assert users.users[admin.id].role == "admin"


async def test_the_last_admin_group_cannot_be_demoted():
    """Same lockout, reached from the group side: change its role and every admin stops being one."""
    admin = _user()
    manager, _, groups = _groups(admin)
    with pytest.raises(LastAdminError, match="demote the group"):
        await manager.update(
            ADMINS.id, name="Admin", role="sre", description=None, projects=(),
            model_profile_id=None,
        )
    assert groups.groups[ADMINS.id].role == "admin"


async def test_the_last_admin_group_cannot_be_deleted():
    admin = _user()
    manager, _, groups = _groups(admin)
    with pytest.raises(LastAdminError, match="delete the group"):
        await manager.delete(ADMINS.id)
    assert groups.deleted == []


async def test_a_group_nobody_is_in_can_be_deleted_freely():
    manager, _, groups = _groups(_user())
    await manager.delete(CONSULTANTS.id)
    assert groups.deleted == [CONSULTANTS.id]


async def test_an_admin_can_be_moved_out_while_another_admin_remains():
    first, second = _user(username="a"), _user(username="b")
    manager, users, _ = _groups(first, second)
    await manager.assign(first.id, SRES.id)
    assert users.users[first.id].role == "sre"
    assert users.users[first.id].projects == ("gcm",)


async def test_reassigning_the_last_admin_to_the_same_group_is_not_blocked():
    """Re-submitting the same group is a no-op, not an attempt to remove the last admin."""
    admin = _user()
    manager, _, _ = _groups(admin)
    assert (await manager.assign(admin.id, ADMINS.id)).role == "admin"


async def test_moving_a_non_admin_is_unaffected_by_the_guard():
    sre = _user(role="sre", username="sre")
    manager, users, _ = _groups(_user(), sre)
    await manager.assign(sre.id, CONSULTANTS.id)
    assert users.users[sre.id].role == "consultant"


async def test_assigning_a_group_that_does_not_exist_is_refused():
    """A stale id from a group someone deleted in another tab must not quietly make them a guest."""
    sre = _user(role="sre", username="sre")
    manager, users, _ = _groups(_user(), sre)
    with pytest.raises(GroupNotFoundError):
        await manager.assign(sre.id, uuid.uuid4())
    assert users.users[sre.id].group_id == SRES.id


async def test_an_account_in_no_group_is_a_guest_with_nothing():
    """The state every new account — and every first Entra sign-in — starts in."""
    nobody = _user(role=None, username="new.hire")
    assert nobody.role == GUEST_ROLE
    assert nobody.group_id is None
    assert nobody.projects == ()


async def test_the_last_admin_cannot_be_deleted():
    admin = _user()
    manager, repo = _manager(admin, _user(role="consultant", username="c"))
    with pytest.raises(LastAdminError, match="delete"):
        await manager.delete(admin.id)
    assert repo.deleted == []



# --- the SSO bypass guard ----------------------------------------------------------------------


async def test_setting_a_password_on_an_entra_account_is_refused():
    """It would create a local credential that bypasses SSO, and with it the tenant's MFA and
    conditional access — the things SSO was adopted for."""
    entra = _user(role="sre", provider="entra", username="hieu.ly")
    manager, repo = _manager(_user(), entra)
    with pytest.raises(NotALocalAccountError, match="entra"):
        await manager.reset_password(entra.id, "a-new-password")
    assert repo.password_updates == []


async def test_setting_a_password_on_a_local_account_still_works():
    local = _user(role="sre", username="sre")
    manager, repo = _manager(_user(), local)
    await manager.reset_password(local.id, "a-new-password")
    assert repo.password_updates == [local.id]


# --- the scope value object ---------------------------------------------------------------------


def test_an_empty_scope_allows_nothing_and_is_not_confused_with_unrestricted():
    """The whole feature turns on this distinction: `names == ()` is a filter matching nothing,
    `names is None` is no filter at all. Getting them the wrong way round would make every new
    account a superuser."""
    empty = ProjectScope.of([])
    assert empty.is_empty
    assert not empty.allows("gcm")
    assert empty.names == ()


def test_an_unrestricted_scope_allows_everything_and_filters_nothing():
    admin = ProjectScope.all()
    assert not admin.is_empty
    assert admin.allows("anything")
    assert admin.names is None


def test_a_scoped_user_is_allowed_only_their_own_projects():
    scope = ProjectScope.of(["gcm", "rxdevs"])
    assert scope.allows("gcm") and scope.allows("rxdevs")
    assert not scope.allows("EVP")
    assert not scope.allows(None)  # an incident with no project belongs to no one but an admin
    assert scope.names == ("gcm", "rxdevs")
