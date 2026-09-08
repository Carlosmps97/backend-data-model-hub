"""Doc 70 §2.4: `views.service.table_views` — vistas GLOBALES de una tabla con
los canvases donde cada una es miembro (explícito por `viewIds` o regla
legacy), publicadas + overlay del changeset. Repositorios mockeados."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.views import service


def test_table_views_sin_changeset_resuelve_canvases_por_membresia(monkeypatch):
    monkeypatch.setattr(service.repository, "list_all", AsyncMock(return_value=[
        {"id": "v1", "projectId": "p1", "name": "B_VU", "schema": "s_vu", "sourceTableIds": ["t1"], "showOnCanvas": True},
        {"id": "v2", "projectId": "p1", "name": "A_VU", "schema": "s_vu", "sourceTableIds": ["t1", "t2"], "showOnCanvas": False},
    ]))
    canvases = AsyncMock(return_value=[
        {"id": "saA", "name": "Canvas A", "tableIds": ["t1"], "viewIds": None},       # legacy → v1 (flag), no v2
        {"id": "saB", "name": "Canvas B", "tableIds": ["t1"], "viewIds": ["v2"]},     # explícito → v2
        {"id": "saC", "name": "Canvas C", "tableIds": ["t9"], "viewIds": ["v1"]},     # miembro sin fuente presente
    ])
    monkeypatch.setattr(service.repository, "canvases_for_views", canvases)

    out = asyncio.run(service.table_views("t1"))
    assert [v["id"] for v in out["views"]] == ["v2", "v1"]            # orden (schema, nombre)
    by = {v["id"]: v for v in out["views"]}
    assert [c["name"] for c in by["v1"]["canvases"]] == ["Canvas A"]
    assert [c["name"] for c in by["v2"]["canvases"]] == ["Canvas B"]
    assert out["total"] == 2
    canvases.assert_awaited_once_with(["v1", "v2"], "t1")


def test_table_views_con_changeset_overlay_de_vistas_y_canvases_draft(monkeypatch):
    monkeypatch.setattr(service.repository, "list_all", AsyncMock(return_value=[
        {"id": "v1", "projectId": "p1", "name": "OLD", "schema": "s", "sourceTableIds": ["t1"], "showOnCanvas": True},
        {"id": "v5", "projectId": "p1", "name": "MOVIDA", "schema": "s", "sourceTableIds": ["t1"], "showOnCanvas": True},
    ]))
    monkeypatch.setattr(service.repository, "canvases_for_views", AsyncMock(return_value=[
        {"id": "saA", "name": "Canvas A", "tableIds": ["t1"], "viewIds": ["v1"]},
    ]))
    changes = {
        "views": {
            "v1": {"op": "upsert", "payload": {"projectId": "p1", "name": "NEW", "schema": "s", "sourceTableIds": ["t1"]}},
            # nueva en el draft, apunta a la tabla → entra
            "v3": {"op": "upsert", "payload": {"projectId": "p1", "name": "DRAFT", "schema": "s", "sourceTableIds": ["t1"]}},
            # re-apuntada a OTRA tabla en el draft → sale
            "v5": {"op": "upsert", "payload": {"projectId": "p1", "name": "MOVIDA", "schema": "s", "sourceTableIds": ["t2"]}},
            # de otra tabla → no entra
            "v4": {"op": "upsert", "payload": {"projectId": "p1", "name": "AJENA", "schema": "s", "sourceTableIds": ["t2"]}},
        },
        "subject_areas": {
            "saNew": {"op": "upsert", "payload": {"projectId": "p1", "name": "Draft canvas", "tableIds": ["t1"], "viewIds": ["v3"]}},
        },
    }
    monkeypatch.setattr(service.cs_repo, "changes_map", AsyncMock(return_value=changes))

    out = asyncio.run(service.table_views("t1", "cs1"))
    by = {v["id"]: v for v in out["views"]}
    assert set(by) == {"v1", "v3"}
    assert by["v1"]["name"] == "NEW" and by["v1"]["schema"] == "s"
    assert [c["name"] for c in by["v1"]["canvases"]] == ["Canvas A"]
    assert [c["name"] for c in by["v3"]["canvases"]] == ["Draft canvas"]
    service.cs_repo.changes_map.assert_awaited_once_with("cs1", ["views", "subject_areas"])


def test_for_table_route_registered(client):
    paths = {r.path for r in client.app.routes}
    assert "/api/views/for-table" in paths
