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


def _wire(monkeypatch, *, sas, tables, counts, views, changes=None, known=None):
    monkeypatch.setattr(service.repository, "canvases_of_project", AsyncMock(return_value=sas))
    # UNA consulta lite: las filas de tabla del proyecto (de ahí salen los ids).
    monkeypatch.setattr(service.repository, "table_rows_of_project", AsyncMock(return_value=tables))
    monkeypatch.setattr(service.repository, "column_counts_for", AsyncMock(return_value=counts))
    monkeypatch.setattr(service.repository, "column_tables_by_ids", AsyncMock(return_value=known or {}))
    monkeypatch.setattr("app.features.views.repository.rows_of_project", AsyncMock(return_value=views))
    monkeypatch.setattr("app.features.changesets.repository.changes_map", AsyncMock(return_value=changes or {}))


def test_project_inventory_publicado_ordena_por_esquema_y_filtra_vistas_por_fuente(monkeypatch):
    _wire(
        monkeypatch,
        sas=[{"id": "sa1", "projectId": "p1", "tableIds": ["t2", "t1"], "viewIds": None},
             {"id": "sa2", "projectId": "p1", "tableIds": ["t1"], "viewIds": []}],
        tables=[{"id": "t1", "projectId": "p1", "physicalName": "M_CLIENTE", "logicalName": "cliente", "schema": "core"},
                {"id": "t2", "projectId": "p1", "physicalName": "A_ZETA", "logicalName": "zeta", "schema": "Analytics"}],
        counts={"t1": 12},
        # Filas SLIM (lo que devuelve `rows_of_project`): conteo, no columnas.
        views=[{"id": "v1", "name": "V_CLI", "schema": "core_vu", "sourceTableIds": ["t1"], "columnCount": 4, "custom": False},
               {"id": "v9", "name": "V_OTRA", "schema": "x", "sourceTableIds": ["t9"], "columnCount": 2, "custom": False}],
    )
    out = asyncio.run(service.project_inventory("p1"))
    assert [t["id"] for t in out["tables"]] == ["t2", "t1"]            # Analytics < core, luego nombre
    assert out["tables"][1]["columnCount"] == 12 and out["tables"][0]["columnCount"] == 0
    assert [v["id"] for v in out["views"]] == ["v1"]                    # v9 no toca el alcance
    assert out["views"][0]["columnCount"] == 4                          # badge vivo; las columnas NO viajan
    assert "sources" not in out["views"][0] and "sql" not in out["views"][0]
    assert out["canvases"] == 2 and out["total"] == {"tables": 2, "views": 1}


def test_project_inventory_draft_overlay_de_canvases_tablas_conteos_y_vistas(monkeypatch):
    _wire(
        monkeypatch,
        sas=[{"id": "sa1", "projectId": "p1", "tableIds": ["t1"], "viewIds": None}],
        tables=[{"id": "t1", "projectId": "p1", "physicalName": "M_CLIENTE", "logicalName": "cliente", "schema": "core"}],
        counts={"t1": 2},
        views=[{"id": "v1", "name": "V_CLI", "schema": "s", "sourceTableIds": ["t1"], "columnCount": 1, "custom": False}],
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
    assert out["views"][0]["schema"] is None and out["canvases"] == 2   # doc del draft normalizado por `view_row`
    assert out["views"][0]["columnCount"] == 0                          # conteo derivado de `sources` del payload


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
        counts={"t1": 3}, views=[],
    )
    out = asyncio.run(service.project_inventory("p1", None))
    assert [t["physicalName"] for t in out["tables"]] == ["A", "B"]   # B no está en ningún canvas
    assert out["total"]["tables"] == 2


def test_project_inventory_consulta_pesadas_por_projectid_no_por_in_de_ids(monkeypatch):
    """Perf/fiabilidad: las DOS consultas caras del inventario se acotan por
    `projectId` (índice, doc 75 D19) y NO con un `$in` de miles de ids:

    - vistas: el `$or` sobre el array jsonb `sourceTableIds` sin proyecto
      escaneaba las vistas de TODOS los proyectos;
    - conteo de columnas: `tableId: {"$in": [2288 ids]}` medía 27 s contra
      0.9 s por proyecto (85 % del tiempo del inventario).

    Juntas disparaban «Couldn't load the project catalog» (timeout) en UDV INT
    FISICO. Medido 2026-09-08; ambas devuelven el mismo resultado."""
    rows_of_project = AsyncMock(return_value=[])
    counts = AsyncMock(return_value={"t1": 1})
    monkeypatch.setattr(service.repository, "canvases_of_project",
                        AsyncMock(return_value=[{"id": "sa1", "projectId": "p1", "tableIds": ["t1"], "viewIds": None}]))
    monkeypatch.setattr(service.repository, "table_rows_of_project",
                        AsyncMock(return_value=[{"id": "t1", "physicalName": "A", "logicalName": "a", "schema": "s"}]))
    monkeypatch.setattr(service.repository, "column_counts_for", counts)
    monkeypatch.setattr(service.repository, "column_tables_by_ids", AsyncMock(return_value={}))
    monkeypatch.setattr("app.features.views.repository.rows_of_project", rows_of_project)
    monkeypatch.setattr("app.features.changesets.repository.changes_map", AsyncMock(return_value={}))
    asyncio.run(service.project_inventory("p1"))
    rows_of_project.assert_awaited_once_with("p1")                   # vistas: por proyecto, slim, sin table_ids
    counts.assert_awaited_once_with("p1")                            # conteos: por proyecto, sin $in


# ── Fila slim de vista + columnas al expandir (perf del árbol, 2026-09-08) ──

def test_view_row_normaliza_fila_slim_y_doc_del_draft():
    """`view_row` acepta las dos formas que conviven tras el overlay."""
    slim = {"id": "v1", "name": "V", "schema": "s", "sourceTableIds": ["t1"], "columnCount": 7, "custom": False}
    assert service.view_row(slim) is slim                              # ya es fila: intacta

    regular = {"id": "v2", "name": "V2", "schema": "s2", "sourceTableIds": ["t1", "t2"],
               "sources": [{"column": "a"}, {"column": "b"}], "sql": "SELECT ...", "description": "x"}
    assert service.view_row(regular) == {
        "id": "v2", "name": "V2", "schema": "s2", "sourceTableIds": ["t1", "t2"],
        "columnCount": 2, "custom": False}                             # `sql`/`description` NO viajan

    custom = {"id": "v3", "name": "V3", "customSql": "SELECT 1", "tableId": "t9",
              "customColumns": [{"name": "c1"}], "sources": [{"column": "ignorada"}]}
    row = service.view_row(custom)
    assert row["custom"] is True and row["columnCount"] == 1            # cuenta customColumns, no sources
    assert row["sourceTableIds"] == ["t9"]                              # fallback legacy `tableId`


def _view_wire(monkeypatch, doc, changes=None):
    monkeypatch.setattr("app.features.views.repository.list_by_ids",
                        AsyncMock(return_value=[doc] if doc else []))
    monkeypatch.setattr("app.features.changesets.repository.changes_map",
                        AsyncMock(return_value=changes or {}))


def test_view_columns_regular_custom_y_404(monkeypatch):
    """Las columnas se piden al expandir; forma UNIFORME para ambas clases."""
    _view_wire(monkeypatch, {"id": "v1", "sources": [
        {"outputAlias": "cli", "column": "CODCLI", "tableId": "t1", "castType": "STRING", "description": "no viaja"}]})
    cols = asyncio.run(service.view_columns("v1"))
    assert cols == [{"outputAlias": "cli", "column": "CODCLI", "tableId": "t1",
                     "table": None, "castType": "STRING", "expression": None}]

    _view_wire(monkeypatch, {"id": "v2", "customSql": "SELECT 1",
                             "customColumns": [{"name": "x", "expression": "1+1"}]})
    assert asyncio.run(service.view_columns("v2")) == [
        {"outputAlias": "x", "column": None, "tableId": None, "table": None,
         "castType": None, "expression": "1+1"}]

    _view_wire(monkeypatch, None)
    assert asyncio.run(service.view_columns("nope")) is None           # el router responde 404


def test_view_columns_aplica_overlay_del_draft(monkeypatch):
    _view_wire(monkeypatch, {"id": "v1", "sources": [{"column": "VIEJA"}]},
               changes={"views": {"v1": {"op": "upsert", "payload": {"sources": [{"column": "NUEVA"}]}}}})
    cols = asyncio.run(service.view_columns("v1", "cs1"))
    assert [c["column"] for c in cols] == ["NUEVA"]
