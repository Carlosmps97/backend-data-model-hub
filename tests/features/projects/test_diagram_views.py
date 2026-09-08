"""`diagram()` y las vistas del canvas.

F3a: canvas LEGACY (sin `viewIds`) → vistas showOnCanvas con ≥1 fuente en el
canvas, resueltas por `views.repository.list_for_canvas`.
Doc 70: canvas con `viewIds` EXPLÍCITO → sólo esas vistas (`list_by_ids`),
filtradas a las que conservan una fuente presente; el canvas sale con
`viewIds` materializado en ambos casos. Repos mockeados con AsyncMock
(patrón test_effective_search.py)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.catalog import repository as catalog_repo
from app.features.changesets import repository as cs_repo
from app.features.projects import service
from app.features.relationships import repository as rel_repo
from app.features.views import repository as views_repo


def _mock_diagram_repos(monkeypatch, views: list[dict], sa: dict | None = None,
                        by_ids: list[dict] | None = None) -> AsyncMock:
    monkeypatch.setattr(
        service.repository, "get_subject_area",
        AsyncMock(return_value=sa or {"id": "sa1", "tableIds": ["t1", "t2"], "layout": {}}))
    monkeypatch.setattr(
        catalog_repo, "list_tables_by_ids",
        AsyncMock(return_value=[{"id": "t1", "physicalName": "CLIENTE"},
                                {"id": "t2", "physicalName": "CUENTA"}]))
    monkeypatch.setattr(catalog_repo, "list_columns_for_tables", AsyncMock(return_value=[]))
    monkeypatch.setattr(rel_repo, "list_for_tables", AsyncMock(return_value=[]))
    mock_views = AsyncMock(return_value=views)
    monkeypatch.setattr(views_repo, "list_for_canvas", mock_views)
    monkeypatch.setattr(views_repo, "list_by_ids", AsyncMock(return_value=by_ids or []))
    return mock_views


def test_diagram_incluye_views_del_canvas(monkeypatch):
    fake = [{"id": "v1", "name": "v_cliente", "sourceTableIds": ["t1"],
             "tableId": "t1", "showOnCanvas": True}]
    mock_views = _mock_diagram_repos(monkeypatch, fake)
    out = asyncio.run(service.diagram("sa1"))
    assert out["views"] == fake
    # La query va acotada a las tablas del canvas (índice sourceTableIds).
    mock_views.assert_awaited_once_with(["t1", "t2"])
    # Doc 70: el canvas legacy sale con la lista MATERIALIZADA de lo que se ve.
    assert out["subjectArea"]["viewIds"] == ["v1"]


def test_diagram_sin_vistas_devuelve_lista_vacia(monkeypatch):
    _mock_diagram_repos(monkeypatch, [])
    out = asyncio.run(service.diagram("sa1"))
    assert out["views"] == []
    assert out["subjectArea"]["viewIds"] == []
    # El resto del contrato del diagrama NO cambia (aditivo).
    assert set(out) == {"subjectArea", "tables", "columns", "relationships", "views"}


def test_diagram_explicito_usa_la_membresia_y_no_la_regla_legacy(monkeypatch):
    legacy = [{"id": "v_legacy", "sourceTableIds": ["t1"], "showOnCanvas": True}]
    members = [{"id": "v9", "sourceTableIds": ["t9"], "showOnCanvas": True},    # fuente fuera del canvas
               {"id": "v1", "sourceTableIds": ["t1"], "showOnCanvas": False}]   # miembro sin flag
    mock_legacy = _mock_diagram_repos(
        monkeypatch, legacy,
        sa={"id": "sa1", "tableIds": ["t1", "t2"], "layout": {}, "viewIds": ["v9", "v1"]},
        by_ids=members)
    out = asyncio.run(service.diagram("sa1"))
    assert [v["id"] for v in out["views"]] == ["v1"]         # v9 sin fuente presente no se dibuja
    mock_legacy.assert_not_awaited()                          # la regla vieja no participa
    views_repo.list_by_ids.assert_awaited_once_with(["v9", "v1"])
    assert out["subjectArea"]["viewIds"] == ["v9", "v1"]     # la lista explícita se devuelve tal cual


def test_diagram_lista_vacia_es_explicitamente_sin_vistas(monkeypatch):
    mock_legacy = _mock_diagram_repos(
        monkeypatch, [{"id": "v1", "sourceTableIds": ["t1"], "showOnCanvas": True}],
        sa={"id": "sa1", "tableIds": ["t1"], "layout": {}, "viewIds": []})
    out = asyncio.run(service.diagram("sa1"))
    assert out["views"] == [] and out["subjectArea"]["viewIds"] == []
    mock_legacy.assert_not_awaited()


def test_diagram_con_changeset_explicito_solo_overlay_de_miembros(monkeypatch):
    _mock_diagram_repos(
        monkeypatch, [],
        sa={"id": "sa1", "tableIds": ["t1"], "layout": {}, "viewIds": ["v1"]},
        by_ids=[{"id": "v1", "name": "old", "sourceTableIds": ["t1"], "showOnCanvas": True}])
    changes = {
        "views": {
            # miembro renombrado en el draft → entra con el payload nuevo
            "v1": {"op": "upsert", "payload": {"name": "new", "sourceTableIds": ["t1"], "showOnCanvas": True}},
            # vista NUEVA del draft que apunta al canvas pero NO es miembro → no se cuela
            "v2": {"op": "upsert", "payload": {"name": "draft", "sourceTableIds": ["t1"], "showOnCanvas": True}},
        },
    }
    monkeypatch.setattr(cs_repo, "changes_map", AsyncMock(return_value=changes))
    out = asyncio.run(service.diagram("sa1", changeset_id="cs1"))
    assert [(v["id"], v["name"]) for v in out["views"]] == [("v1", "new")]


def test_diagram_con_changeset_legacy_admite_vistas_nuevas_del_draft(monkeypatch):
    _mock_diagram_repos(monkeypatch, [])
    changes = {"views": {"v2": {"op": "upsert", "payload": {"name": "draft", "sourceTableIds": ["t1"],
                                                            "showOnCanvas": True}}}}
    monkeypatch.setattr(cs_repo, "changes_map", AsyncMock(return_value=changes))
    out = asyncio.run(service.diagram("sa1", changeset_id="cs1"))
    assert [v["id"] for v in out["views"]] == ["v2"]
    assert out["subjectArea"]["viewIds"] == ["v2"]
