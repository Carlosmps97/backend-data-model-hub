"""Links de una tabla en TODOS los canvases (Properties · Links): la tabla es
canónica, así que el panel muestra también las relaciones visibles sólo en
otros canvases — `table_links` resuelve nombres y canvases (ambas tablas)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.relationships import service

REL = {"id": "r1", "projectId": "p1", "parentTableId": "tA", "childTableId": "tB",
       "pairs": [{"parentColumnId": "cA", "childColumnId": "cB", "roleName": None}],
       "parentCardinality": "zero-one", "childCardinality": "zero-many", "identifying": False}


def test_table_links_sin_relaciones(monkeypatch):
    monkeypatch.setattr(service.repository, "list_for_tables", AsyncMock(return_value=[]))
    out = asyncio.run(service.table_links("tX", None))
    assert out == {"links": [], "total": 0}


def test_table_links_publicado_resuelve_nombres_y_canvases(monkeypatch):
    monkeypatch.setattr(service.repository, "list_for_tables", AsyncMock(return_value=[REL]))
    monkeypatch.setattr(service.repository, "canvases_containing", AsyncMock(return_value=[
        {"id": "sa1", "name": "Ventas", "tableIds": ["tA", "tB"]},
        {"id": "sa2", "name": "Riesgos", "tableIds": ["tA"]},  # NO tiene la otra tabla
    ]))

    async def _pub(collection, flt=None, **kwargs):
        return {"canonical_tables": [{"id": "tA", "physicalName": "CLIENTE"},
                                     {"id": "tB", "physicalName": "CUENTA"}],
                "canonical_columns": [{"id": "cA", "physicalName": "ID_CLI"},
                                      {"id": "cB", "physicalName": "ID_CTA"}]}[collection]

    monkeypatch.setattr(service.cs_repo, "published", _pub)
    out = asyncio.run(service.table_links("tA", None))
    assert out["total"] == 1
    link = out["links"][0]
    assert link["parentTableName"] == "CLIENTE" and link["childTableName"] == "CUENTA"
    assert link["pairs"][0]["parentColumnName"] == "ID_CLI"
    assert link["pairs"][0]["childColumnName"] == "ID_CTA"
    assert link["parentCardinality"] == "zero-one"
    # Visible sólo en los canvases que contienen AMBAS tablas.
    assert link["canvases"] == [{"id": "sa1", "name": "Ventas"}]


def test_table_links_canvases_incluye_canvas_del_draft(monkeypatch):
    """Un canvas creado EN EL DRAFT que contiene ambas tablas cuenta como
    canvas visible de la relación (overlay de subject_areas)."""
    monkeypatch.setattr(service.repository, "list_for_tables", AsyncMock(return_value=[REL]))
    monkeypatch.setattr(service.repository, "canvases_containing", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.cs_repo, "changes_map", AsyncMock(return_value={
        "subject_areas": {"sa9": {"op": "upsert", "payload": {"projectId": "p1", 
            "name": "canvas001", "tableIds": ["tA", "tB"]}}},
    }))
    monkeypatch.setattr(service.cs_repo, "published", AsyncMock(return_value=[]))
    out = asyncio.run(service.table_links("tA", "cs1"))
    assert out["total"] == 1
    assert out["links"][0]["canvases"] == [{"id": "sa9", "name": "canvas001"}]


def test_table_links_overlay_borra_y_agrega(monkeypatch):
    """El draft borra r1 y agrega r2 (hacia una tabla NUEVA aún no publicada):
    el resultado refleja el efectivo y toma el nombre del payload."""
    monkeypatch.setattr(service.repository, "list_for_tables", AsyncMock(return_value=[REL]))
    monkeypatch.setattr(service.repository, "canvases_containing", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.cs_repo, "changes_map", AsyncMock(return_value={
        "relationships": {
            "r1": {"op": "delete"},
            "r2": {"op": "upsert", "payload": {"projectId": "p1", 
                "parentTableId": "tC", "childTableId": "tA",
                "pairs": [{"parentColumnId": "cC", "childColumnId": "cA2"}],
                "parentCardinality": "one", "childCardinality": "zero-many",
                "identifying": True}},
        },
        "canonical_tables": {"tC": {"op": "upsert", "payload": {"projectId": "p1", "physicalName": "PRODUCTO"}}},
    }))
    monkeypatch.setattr(service.cs_repo, "published", AsyncMock(return_value=[]))
    out = asyncio.run(service.table_links("tA", "cs1"))
    assert out["total"] == 1
    link = out["links"][0]
    assert link["id"] == "r2" and link["identifying"] is True
    assert link["parentTableName"] == "PRODUCTO"   # del payload (no publicada)
    assert link["pairs"][0]["childColumnName"] == "cA2"  # sin nombre conocido → id
