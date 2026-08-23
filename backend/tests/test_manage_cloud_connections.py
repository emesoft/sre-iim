"""Unit tests for ManageCloudConnections — no DB, no network."""

import pytest

from app.application.cloud_connections.manage import ManageCloudConnections
from app.infrastructure.security.encryptor import Encryptor

pytestmark = pytest.mark.asyncio

_KEY = "zH8yV2m3sVW6tG5v9pQwQflR4z1sT8y3lU9wA0b3iF4="


class FakeRepo:
    def __init__(self):
        self.rows = {}
        self.raise_on_add = None

    async def add(self, connection):
        if self.raise_on_add:
            raise self.raise_on_add
        connection.id = "generated-id"
        self.rows[connection.id] = connection
        return connection

    async def get(self, connection_id):
        return self.rows.get(connection_id)

    async def list(self):
        return list(self.rows.values())

    async def delete(self, connection_id):
        self.rows.pop(connection_id, None)


class FakeFetcher:
    def __init__(self, should_fail=False):
        self._should_fail = should_fail

    async def list_alarms(self, connection):
        if self._should_fail:
            raise RuntimeError("access denied")
        return []


class FakeUnitOfWork:
    def __init__(self):
        self.rolled_back = False

    async def commit(self):
        pass

    async def rollback(self):
        self.rolled_back = True


def _manager(fetcher=None, repo=None, uow=None):
    return ManageCloudConnections(
        connections=repo or FakeRepo(), encryptor=Encryptor(_KEY),
        fetcher=fetcher or FakeFetcher(), uow=uow or FakeUnitOfWork(),
    )


async def test_create_encrypts_access_key_credentials():
    manager = _manager()
    connection = await manager.create(
        project="GCM", env="prod", region="ap-southeast-1", auth_type="access_key",
        access_key_id="AKIAEXAMPLE", secret_access_key="supersecret",
    )
    assert connection.encrypted_access_key_id != "AKIAEXAMPLE"
    assert connection.encrypted_secret_access_key != "supersecret"


async def test_create_sso_connection_has_no_encrypted_fields():
    manager = _manager()
    connection = await manager.create(
        project="GCM", env="prod", region="ap-southeast-1", auth_type="sso",
        sso_profile_name="GCM-Prod-ReadOnlyAccess",
    )
    assert connection.encrypted_access_key_id is None
    assert connection.encrypted_secret_access_key is None


async def test_test_connection_reports_success():
    manager = _manager(fetcher=FakeFetcher(should_fail=False))
    connection = await manager.create(
        project="GCM", env="prod", region="ap-southeast-1", auth_type="sso", sso_profile_name="p"
    )
    ok, error = await manager.test(connection.id)
    assert ok is True
    assert error is None


async def test_test_connection_reports_failure():
    manager = _manager(fetcher=FakeFetcher(should_fail=True))
    connection = await manager.create(
        project="GCM", env="prod", region="ap-southeast-1", auth_type="sso", sso_profile_name="p"
    )
    ok, error = await manager.test(connection.id)
    assert ok is False
    assert error == "access denied"


async def test_create_unknown_project_raises_unknown_project_error():
    from sqlalchemy.exc import IntegrityError

    from app.domain.projects.errors import UnknownProjectError

    repo = FakeRepo()
    repo.raise_on_add = IntegrityError("stmt", {}, Exception("fk violation"))
    uow = FakeUnitOfWork()
    manager = _manager(repo=repo, uow=uow)
    with pytest.raises(UnknownProjectError):
        await manager.create(
            project="NOPE", env="prod", region="us-east-1", auth_type="sso",
            sso_profile_name="p",
        )
    assert uow.rolled_back is True
