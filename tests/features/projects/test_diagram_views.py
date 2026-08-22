"""`diagram()` gana la clave `views` (F3a): vistas showOnCanvas con ≥1 fuente
en el canvas, resueltas por `views.repository.list_for_canvas`. Sin overlay de
changeset (las vistas son REST directo, no changeset-aware). Repos mockeados
con AsyncMock (patrón test_effective_search.py)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.catalog import repository as catalog_repo
from app.features.projects import service
from app.features.relationships import repository as rel_repo
from app.features.views import repository as views_repo


def _mock_diagram_repos(monkeypatch, views: list[dict]) -> AsyncMock:
    monkeypatch.setattr(
        service.repository, "get_subject_area",
        AsyncMock(return_value={"id": "sa1", "tableIds": ["t1", "t2"], "layout": {}}))
    monkeypatch.setattr(
        catalog_repo, "list_tables_by_ids",
        AsyncMock(return_value=[{"id": "t1", "physicalName": "CLIENTE"},
                                {"id": "t2", "physicalName": "CUENTA"}]))
    monkeypatch.setattr(catalog_repo, "list_columns_for_tables", AsyncMock(return_value=[]))
    monkeypatch.setattr(rel_repo, "list_for_tables", AsyncMock(return_value=[]))
    mock_views = AsyncMock(return_value=views)
    monkeypatch.setattr(views_repo, "list_for_canvas", mock_views)
    return mock_views


def test_diagram_incluye_views_del_canvas(monkeypatch):
    fake = [{"id": "v1", "name": "v_cliente", "sourceTableIds": ["t1"],
             "tableId": "t1", "showOnCanvas": True}]
    mock_views = _mock_diagram_repos(monkeypatch, fake)
    out = asyncio.run(service.diagram("sa1"))
    assert out["views"] == fake
    # La query va acotada a las tablas del canvas (índice sourceTableIds).
    mock_views.assert_awaited_once_with(["t1", "t2"])


def test_diagram_sin_vistas_devuelve_lista_vacia(monkeypatch):
    _mock_diagram_repos(monkeypatch, [])
    out = asyncio.run(service.diagram("sa1"))
    assert out["views"] == []
    # El resto del contrato del diagrama NO cambia (aditivo).
    assert set(out) == {"subjectArea", "tables", "columns", "relationships", "views"}
