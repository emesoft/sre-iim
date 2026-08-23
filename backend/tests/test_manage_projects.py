"""Unit tests for ManageProjects — no DB, no network."""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.application.projects.manage import ManageProjects
from app.domain.projects.entities import Project
from app.domain.projects.errors import ProjectInUseError, ProjectNameTakenError

pytestmark = pytest.mark.asyncio


class FakeRepo:
    def __init__(self):
        self.rows = {}
        self.raise_on_add = None
        self.raise_on_delete = None

    async def add(self, project):
        if self.raise_on_add:
            raise self.raise_on_add
        project.id = uuid.uuid4()
        self.rows[project.id] = project
        return project

    async def get(self, project_id):
        return self.rows.get(project_id)

    async def list(self):
        return list(self.rows.values())

    async def delete(self, project_id):
        if self.raise_on_delete:
            raise self.raise_on_delete
        self.rows.pop(project_id, None)


class FakeUnitOfWork:
    def __init__(self):
        self.rolled_back = False

    async def commit(self):
        pass

    async def rollback(self):
        self.rolled_back = True


def _manager(repo=None, uow=None):
    return ManageProjects(projects=repo or FakeRepo(), uow=uow or FakeUnitOfWork())


async def test_create_returns_the_project():
    manager = _manager()
    project = await manager.create("EVP")
    assert project.name == "EVP"
    assert project.id is not None


async def test_create_duplicate_name_raises_project_name_taken():
    repo = FakeRepo()
    repo.raise_on_add = IntegrityError("stmt", {}, Exception("dup"))
    uow = FakeUnitOfWork()
    manager = _manager(repo=repo, uow=uow)
    with pytest.raises(ProjectNameTakenError):
        await manager.create("EVP")
    assert uow.rolled_back is True


async def test_delete_unknown_id_raises_value_error():
    manager = _manager()
    with pytest.raises(ValueError):
        await manager.delete(uuid.uuid4())


async def test_delete_referenced_project_raises_project_in_use():
    repo = FakeRepo()
    project = await repo.add(Project(name="EVP"))
    repo.raise_on_delete = IntegrityError("stmt", {}, Exception("fk violation"))
    uow = FakeUnitOfWork()
    manager = _manager(repo=repo, uow=uow)
    with pytest.raises(ProjectInUseError):
        await manager.delete(project.id)
    assert uow.rolled_back is True
