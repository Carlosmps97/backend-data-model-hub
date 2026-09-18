"""Agregación pura de vistas del reporte (`reporting.service.view_rows`)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.reporting import service
from app.features.reporting.service import view_rows

_NAMES = {"t1": "core.CLIENTE", "t2": "core.CUENTA"}


def _views():
    return [
        {"id": "v1", "name": "VW_CLIENTE", "schema": "core_vu",
         "sourceTableIds": ["t1"],
         "showOnCanvas": True, "description": "Vista 1:1", "sql": "SELECT …",
         "sources": [
             {"tableId": "t1", "column": "CODCLI", "outputAlias": "COD_CLIENTE",
              "castType": "VARCHAR(30)"},
             {"tableId": "t1", "expression": "UPPER(NBRCLI)", "outputAlias": "NOMBRE"},
             {"tableId": "t1"},  # source vacío (sin column ni expression) → se omite
         ]},
        {"id": "v2", "name": "VW_MIX", "schema": "core_vu",
         "sourceTableIds": ["t1", "t2"],
         "customSql": "CREATE VIEW core_vu.VW_MIX AS SELECT t2.CODCTA FROM core.CLIENTE t1, core.CUENTA t2",
         "sources": [{"tableId": "t2", "column": "CODCTA"}]},
        # legacy: sin sourceTableIds, solo tableId
        {"id": "v3", "name": "VW_LEGACY", "tableId": "t1",
         "sources": [{"column": "CODCLI"}]},
    ]


def test_view_rows_resolves_sources_and_columns():
    rows = view_rows(_views(), _NAMES)
    by_id = {r["id"]: r for r in rows}

    v1 = by_id["v1"]
    assert v1["sourceTables"] == ["core.CLIENTE"]
    assert "filter" not in v1 and "joinOverride" not in v1     # doc 91 D2/D5
    assert v1["customSql"] is None
    # el source vacío se omite; alias cae al nombre de columna si no hay alias
    assert len(v1["columns"]) == 2
    assert v1["columns"][0] == {
        "outputAlias": "COD_CLIENTE", "sourceTable": "core.CLIENTE",
        "sourceColumn": "CODCLI", "castType": "VARCHAR(30)", "expression": None}
    assert v1["columns"][1]["expression"] == "UPPER(NBRCLI)"

    v2 = by_id["v2"]
    assert v2["sourceTables"] == ["core.CLIENTE", "core.CUENTA"]
    assert v2["customSql"].startswith("CREATE VIEW core_vu.VW_MIX")   # User-Defined SQL viaja al reporte
    assert v2["columns"][0]["sourceTable"] == "core.CUENTA"


def test_view_rows_legacy_table_id_and_sort():
    rows = view_rows(_views(), _NAMES)
    # legacy tableId → sourceTableIds derivado; source sin tableId hereda la 1ª fuente
    v3 = next(r for r in rows if r["id"] == "v3")
    assert v3["sourceTables"] == ["core.CLIENTE"]
    assert v3["columns"][0]["sourceTable"] == "core.CLIENTE"
    # orden por (schema, nombre): v3 sin schema va primero
    assert [r["id"] for r in rows] == ["v3", "v1", "v2"]


def test_view_rows_canvases_por_membresia_explicita_y_legacy():
    """Doc 70: `canvases` = nombres donde la vista es miembro visible —
    explícito por `viewIds`, o regla legacy (showOnCanvas ∧ fuente) en canvases
    sin lista. Sin canvases → lista vacía (aditivo)."""
    sas = [
        {"id": "saA", "name": "Legacy", "tableIds": ["t1"], "viewIds": None},         # v1 por flag; v2 sin flag no
        {"id": "saB", "name": "Explicit", "tableIds": ["t1", "t2"], "viewIds": ["v2"]},
        {"id": "saC", "name": "Elsewhere", "tableIds": ["t9"], "viewIds": ["v1"]},    # sin fuente presente
    ]
    rows = view_rows(_views(), _NAMES, sas)
    by_id = {r["id"]: r for r in rows}
    assert by_id["v1"]["canvases"] == ["Legacy"]
    assert by_id["v2"]["canvases"] == ["Explicit"]
    assert by_id["v3"]["canvases"] == []                    # legacy sin flag
    assert view_rows(_views(), _NAMES)[0]["canvases"] == []  # sin canvases: aditivo


def test_list_view_rows_filtra_por_schema(monkeypatch):
    """Database Explorer (doc 18 §5): `?schema=` baja el filtro al repository
    (server-side) y NO aplica el cap de vistas sin filtrar."""
    captured: dict = {}

    async def fake_views_for_tables(project_id, table_ids=None, limit=None, schema=None):
        captured.update(project_id=project_id, table_ids=table_ids, limit=limit, schema=schema)
        return [{"id": "v1", "name": "V", "schema": "core_vu",
                 "sourceTableIds": [], "sources": []}]

    monkeypatch.setattr(service.repository, "views_for_tables", fake_views_for_tables)
    monkeypatch.setattr(service.repository, "table_names", AsyncMock(return_value={}))
    monkeypatch.setattr(service.repository, "_subject_areas", AsyncMock(return_value=[]))
    rows = asyncio.run(service.list_view_rows("p1", schema="core_vu"))
    assert captured["project_id"] == "p1"
    assert captured["schema"] == "core_vu"
    assert captured["limit"] is None
    assert rows[0]["id"] == "v1"
