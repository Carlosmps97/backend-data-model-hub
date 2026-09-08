"""Fixtures compartidas de los tests de `changesets`.

Doc 75: los services consultan `projects.repository` (proyecto vivo, conteos
del borrado). Por defecto el proyecto del changeset EXISTE y está vacío — los
tests que prueban el proyecto borrado sobreescriben `get_project` con None."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.features.changesets import service


@pytest.fixture(autouse=True)
def _project_alive(monkeypatch):
    monkeypatch.setattr(service.projects_repo, "get_project",
                        AsyncMock(side_effect=lambda pid: {"id": pid, "name": f"Project {pid}"}))
    monkeypatch.setattr(service.projects_repo, "count_scope",
                        AsyncMock(return_value={"tables": 0, "columns": 0, "views": 0, "relationships": 0,
                                                "schemas": 0, "folders": 0, "canvases": 0}))
    monkeypatch.setattr(service.projects_repo, "cascade_delete", AsyncMock(return_value={}))
