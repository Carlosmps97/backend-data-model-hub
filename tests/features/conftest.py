"""Doc 109: los tests unitarios que pasan por el apply de Data Standards (los de
Data Standards y los de DDL rules) mockean cada repositorio a mano; el bloque
nuevo de themes arranca VACÍO (y sus escrituras como mocks) para que esos tests
no vayan a la BD. Un test de themes lo vuelve a parchear. Los de integración
(`tests/integration`) usan los repositorios reales."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def _themes_vacios(monkeypatch):
    from app.features.data_standards import service

    monkeypatch.setattr(service.themes_repo, "list_themes", AsyncMock(return_value=[]))
    for fn in ("create_theme", "update_theme", "delete_theme", "restore_themes"):
        monkeypatch.setattr(service.themes_repo, fn, AsyncMock())
