"""Doc 72 §3 — Object Inspector: dossier de una tabla o vista en UNA request,
efectivo-aware, sin canvas. Repositorios y colaboradores mockeados."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.catalog import service


def _names_stub(folders: dict, projects: dict):
    async def _names(coll: str, ids: list[str]) -> dict:
        pool = folders if coll == "folders" else projects
        return {i: pool[i] for i in ids if i in pool}
    return _names


def test_inspect_table_none_si_no_existe(monkeypatch):
    monkeypatch.setattr(service.repository, "list_tables_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "list_columns", AsyncMock(return_value=[]))
    assert asyncio.run(service.inspect_table("t404")) is None


def test_inspect_table_compone_colaboradores_y_overlay_de_columnas(monkeypatch):
    monkeypatch.setattr(service.repository, "list_tables_by_ids", AsyncMock(return_value=[
        {"id": "t1", "projectId": "p1", "physicalName": "M_CLIENTE", "logicalName": "cliente", "schema": "core"}]))
    monkeypatch.setattr(service.repository, "list_columns", AsyncMock(return_value=[
        {"id": "c2", "tableId": "t1", "physicalName": "B", "logicalName": "b", "dataType": "INT", "ordinal": 2},
        {"id": "c1", "tableId": "t1", "physicalName": "A", "logicalName": "a", "dataType": "INT", "ordinal": 1},
    ]))
    monkeypatch.setattr("app.features.changesets.repository.changes_map", AsyncMock(return_value={
        "canonical_tables": {"t1": {"op": "upsert", "payload": {"projectId": "p1", "physicalName": "M_PERSONA", "logicalName": "persona", "schema": "core"}}},
        "canonical_columns": {
            "c3": {"op": "upsert", "payload": {"projectId": "p1", "tableId": "t1", "physicalName": "C", "logicalName": "c", "dataType": "INT", "ordinal": 3}},
            "c2": {"op": "delete"},
            "c9": {"op": "upsert", "payload": {"projectId": "p1", "tableId": "t9", "physicalName": "Z", "logicalName": "z", "dataType": "INT", "ordinal": 0}},
        },
    }))
    links = AsyncMock(return_value={"links": [{"id": "r1"}], "total": 1})
    views = AsyncMock(return_value={"views": [{"id": "v1"}], "total": 1})
    usage = AsyncMock(return_value={"usage": [{"canvasId": "sa1"}], "total": 1})
    monkeypatch.setattr("app.features.relationships.service.table_links", links)
    monkeypatch.setattr("app.features.views.service.table_views", views)
    monkeypatch.setattr(service, "table_usage", usage)

    out = asyncio.run(service.inspect_table("t1", "cs1"))
    assert out["table"]["physicalName"] == "M_PERSONA"                  # overlay de la tabla
    assert [c["id"] for c in out["columns"]] == ["c1", "c3"]            # c2 borrada, c3 nueva, c9 de otra tabla fuera
    assert out["links"] == [{"id": "r1"}] and out["views"] == [{"id": "v1"}] and out["usage"] == [{"canvasId": "sa1"}]
    links.assert_awaited_once_with("t1", "cs1")
    views.assert_awaited_once_with("t1", "cs1")
    usage.assert_awaited_once_with("t1", "cs1")


def test_inspect_view_none_si_no_existe(monkeypatch):
    monkeypatch.setattr("app.features.views.repository.list_by_ids", AsyncMock(return_value=[]))
    assert asyncio.run(service.inspect_view("v404")) is None


def test_inspect_view_resuelve_fuentes_en_orden_y_canvases_por_membresia(monkeypatch):
    monkeypatch.setattr("app.features.views.repository.list_by_ids", AsyncMock(return_value=[
        {"id": "v1", "projectId": "p1", "name": "V_CLI", "schema": "s_vu", "sourceTableIds": ["t1", "t2"], "showOnCanvas": True, "sources": []}]))
    monkeypatch.setattr(service.repository, "list_tables_by_ids", AsyncMock(return_value=[
        {"id": "t2", "projectId": "p1", "physicalName": "M_CUENTA", "logicalName": "cuenta", "schema": "core"},
        {"id": "t1", "projectId": "p1", "physicalName": "M_CLIENTE", "logicalName": "cliente", "schema": "core"}]))
    monkeypatch.setattr(service.repository, "canvases_with_tables", AsyncMock(return_value=[
        {"id": "saA", "name": "A", "projectId": "p1", "folderId": None, "tableIds": ["t1"], "viewIds": None},    # legacy: flag → sí
        {"id": "saB", "name": "B", "projectId": "p1", "folderId": "f1", "tableIds": ["t1"], "viewIds": []},      # explícito vacío → no
        {"id": "saC", "name": "C", "projectId": "p1", "folderId": None, "tableIds": ["t2"], "viewIds": ["v1"]},  # explícito → sí
    ]))
    monkeypatch.setattr(service.repository, "names_by_ids", _names_stub({"f1": "Carpeta"}, {"p1": "Proyecto"}))

    out = asyncio.run(service.inspect_view("v1"))
    assert [s["physicalName"] for s in out["sources"]] == ["M_CLIENTE", "M_CUENTA"]   # orden de las fuentes de la vista
    assert [u["canvas"] for u in out["usage"]] == ["A", "C"]
    assert out["usage"][0]["project"] == "Proyecto" and out["view"]["id"] == "v1"


def test_inspect_view_draft_overlay_de_vista_fuentes_y_canvas_nuevo(monkeypatch):
    monkeypatch.setattr("app.features.views.repository.list_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "list_tables_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "canvases_with_tables", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "names_by_ids", _names_stub({}, {}))
    monkeypatch.setattr("app.features.changesets.repository.changes_map", AsyncMock(return_value={
        "views": {"vNew": {"op": "upsert", "payload": {"projectId": "p1", "name": "V_NUEVA", "sourceTableIds": ["tNew"], "sources": []}}},
        "canonical_tables": {"tNew": {"op": "upsert", "payload": {"projectId": "p1", "physicalName": "M_NUEVA", "logicalName": "nueva"}}},
        "subject_areas": {"saNew": {"op": "upsert", "payload": {"projectId": "pNew", "name": "Canvas nuevo",
                                                                   "tableIds": ["tNew"], "viewIds": ["vNew"]}}},
        "projects": {"pNew": {"op": "upsert", "payload": {"projectId": "p1", "name": "Proyecto nuevo"}}},
    }))
    out = asyncio.run(service.inspect_view("vNew", "cs1"))
    assert out["view"]["name"] == "V_NUEVA" and out["view"]["schema"] is None
    assert [s["physicalName"] for s in out["sources"]] == ["M_NUEVA"]
    assert [(u["canvas"], u["project"]) for u in out["usage"]] == [("Canvas nuevo", "Proyecto nuevo")]
