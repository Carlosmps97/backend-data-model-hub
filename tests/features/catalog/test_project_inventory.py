"""Doc 72 §1 — inventario del proyecto para el Explorer en UNA request: alcance
por `projectId` (doc 75 §6.2), tablas con columnCount, vistas por fuentes;
overlay del draft. Repositorios mockeados."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.catalog import service
from app.features.catalog.service import adjust_column_counts


def test_adjust_column_counts_alta_baja_edicion_y_mudanza():
    counts = {"t1": 3, "t2": 1, "t3": 0}
    changes = {
        "cNew": {"op": "upsert", "payload": {"projectId": "p1", "tableId": "t1"}},     # alta → t1 +1
        "cDel": {"op": "delete"},                                    # baja publicada de t2 → −1
        "cEdit": {"op": "upsert", "payload": {"projectId": "p1", "tableId": "t1"}},    # edición en sitio → 0
        "cMove": {"op": "upsert", "payload": {"projectId": "p1", "tableId": "t2"}},    # t1 → t2
        "cOut": {"op": "upsert", "payload": {"projectId": "p1", "tableId": "t9"}},     # fuera del alcance → ignorada
        "cGhost": {"op": "delete"},                                  # baja sin publicado → ignorada
        "cFloor": {"op": "delete"},                                  # t3 ya en 0 → no baja de 0
    }
    known = {"cDel": "t2", "cEdit": "t1", "cMove": "t1", "cFloor": "t3"}
    out = adjust_column_counts(counts, changes, known, {"t1", "t2", "t3"})
    assert out == {"t1": 3, "t2": 1, "t3": 0}


def _wire(monkeypatch, *, sas, tables, counts, views, changes=None, known=None, table_ids=None):
    monkeypatch.setattr(service.repository, "canvases_of_project", AsyncMock(return_value=sas))
    monkeypatch.setattr(service.repository, "table_ids_of_project",
                        AsyncMock(return_value=table_ids if table_ids is not None else [t["id"] for t in tables]))
    monkeypatch.setattr(service.repository, "list_tables_by_ids", AsyncMock(return_value=tables))
    monkeypatch.setattr(service.repository, "column_counts_for", AsyncMock(return_value=counts))
    monkeypatch.setattr(service.repository, "column_tables_by_ids", AsyncMock(return_value=known or {}))
    monkeypatch.setattr("app.features.views.repository.list_all", AsyncMock(return_value=views))
    monkeypatch.setattr("app.features.changesets.repository.changes_map", AsyncMock(return_value=changes or {}))


def test_project_inventory_publicado_ordena_por_esquema_y_filtra_vistas_por_fuente(monkeypatch):
    _wire(
        monkeypatch,
        sas=[{"id": "sa1", "projectId": "p1", "tableIds": ["t2", "t1"], "viewIds": None},
             {"id": "sa2", "projectId": "p1", "tableIds": ["t1"], "viewIds": []}],
        tables=[{"id": "t1", "projectId": "p1", "physicalName": "M_CLIENTE", "logicalName": "cliente", "schema": "core"},
                {"id": "t2", "projectId": "p1", "physicalName": "A_ZETA", "logicalName": "zeta", "schema": "Analytics"}],
        counts={"t1": 12},
        views=[{"id": "v1", "projectId": "p1", "name": "V_CLI", "schema": "core_vu", "sourceTableIds": ["t1"], "showOnCanvas": True},
               {"id": "v9", "projectId": "p1", "name": "V_OTRA", "schema": "x", "sourceTableIds": ["t9"], "showOnCanvas": True}],
    )
    out = asyncio.run(service.project_inventory("p1"))
    assert [t["id"] for t in out["tables"]] == ["t2", "t1"]            # Analytics < core, luego nombre
    assert out["tables"][1]["columnCount"] == 12 and out["tables"][0]["columnCount"] == 0
    assert [v["id"] for v in out["views"]] == ["v1"]                    # v9 no toca el alcance
    assert out["canvases"] == 2 and out["total"] == {"tables": 2, "views": 1}


def test_project_inventory_draft_overlay_de_canvases_tablas_conteos_y_vistas(monkeypatch):
    _wire(
        monkeypatch,
        sas=[{"id": "sa1", "projectId": "p1", "tableIds": ["t1"], "viewIds": None}],
        tables=[{"id": "t1", "projectId": "p1", "physicalName": "M_CLIENTE", "logicalName": "cliente", "schema": "core"}],
        counts={"t1": 2},
        views=[{"id": "v1", "projectId": "p1", "name": "V_CLI", "schema": "s", "sourceTableIds": ["t1"], "showOnCanvas": True}],
        changes={
            "subject_areas": {
                "saNew": {"op": "upsert", "payload": {"projectId": "p1", "name": "Nuevo", "tableIds": ["tNew"]}},
                "saOther": {"op": "upsert", "payload": {"projectId": "p2", "name": "Ajeno", "tableIds": ["t7"]}},
            },
            "canonical_tables": {
                "t1": {"op": "upsert", "payload": {"projectId": "p1", "physicalName": "M_PERSONA", "logicalName": "persona", "schema": "core"}},
                "tNew": {"op": "upsert", "payload": {"projectId": "p1", "physicalName": "M_NUEVA", "logicalName": "nueva", "schema": "core"}},
                "t7": {"op": "upsert", "payload": {"projectId": "p2", "physicalName": "AJENA", "logicalName": "ajena"}},
            },
            "canonical_columns": {
                "cNew": {"op": "upsert", "payload": {"projectId": "p1", "tableId": "t1", "physicalName": "X"}},
                "cNew2": {"op": "upsert", "payload": {"projectId": "p1", "tableId": "tNew", "physicalName": "Y"}},
            },
            "views": {
                "v1": {"op": "upsert", "payload": {"projectId": "p1", "name": "V_CLI", "sourceTableIds": ["t9"], "sources": []}},     # re-apuntada fuera
                "vNew": {"op": "upsert", "payload": {"projectId": "p1", "name": "V_NUEVA", "sourceTableIds": ["tNew"], "sources": []}},
            },
        },
    )
    out = asyncio.run(service.project_inventory("p1", "cs1"))
    by = {t["id"]: t for t in out["tables"]}
    assert set(by) == {"t1", "tNew"}                                    # t7 (de otro proyecto) fuera
    assert by["t1"]["physicalName"] == "M_PERSONA" and by["t1"]["columnCount"] == 3
    assert by["tNew"]["columnCount"] == 1
    assert [v["id"] for v in out["views"]] == ["vNew"]                  # v1 re-apuntada desaparece
    assert out["views"][0]["schema"] is None and out["canvases"] == 2   # normalizada por ViewDoc


def test_project_inventory_sin_canvases_no_consulta_vistas(monkeypatch):
    _wire(monkeypatch, sas=[], tables=[], counts={}, views=[{"id": "vX", "name": "X", "sourceTableIds": ["t1"]}])
    out = asyncio.run(service.project_inventory("p1"))
    assert out == {"tables": [], "views": [], "canvases": 0, "total": {"tables": 0, "views": 0}}


def test_inventario_incluye_tablas_del_proyecto_fuera_de_canvas(monkeypatch):
    """Doc 75 §6.2: el alcance es el projectId — una tabla sin canvas también se lista."""
    _wire(
        monkeypatch,
        sas=[{"id": "sa1", "projectId": "p1", "tableIds": ["t1"], "viewIds": None}],
        tables=[{"id": "t1", "projectId": "p1", "physicalName": "A", "logicalName": "a", "schema": "s"},
                {"id": "t2", "projectId": "p1", "physicalName": "B", "logicalName": "b", "schema": "s"}],
        counts={"t1": 3}, views=[], table_ids=["t1", "t2"],
    )
    out = asyncio.run(service.project_inventory("p1", None))
    assert [t["physicalName"] for t in out["tables"]] == ["A", "B"]   # B no está en ningún canvas
    assert out["total"]["tables"] == 2
