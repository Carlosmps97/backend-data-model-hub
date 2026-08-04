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
