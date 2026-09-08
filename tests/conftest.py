"""Fixtures compartidas de tests.

`client` NO usa el TestClient como context manager a propósito: así NO se
dispara el lifespan de la app (que intentaría conectar a la BD). Los tests de
esta fase (identidad, health) no tocan la base de datos.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def project_client(client: TestClient) -> TestClient:
    """Cliente con la dependencia `alive_project` sobreescrita (sin DB): las
    rutas `/api/projects/{project_id}/…` (doc 75 D4) aceptan cualquier id."""
    from app.features.projects.deps import alive_project

    client.app.dependency_overrides[alive_project] = lambda project_id: project_id
    yield client
    client.app.dependency_overrides.pop(alive_project, None)
