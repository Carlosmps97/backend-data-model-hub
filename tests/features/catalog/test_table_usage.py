"""Uso de una tabla (V3, doc 19 §12b): filas proyecto›carpeta›canvas + overlay."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import app.features.changesets.repository as cs_repo
from app.features.catalog import service


def _names_stub(folders: dict, projects: dict):
    async def _names(coll: str, ids: list[str]) -> dict:
        pool = folders if coll == "folders" else projects
        return {i: pool[i] for i in ids if i in pool}
    return _names


def test_table_usage_ordena_proyecto_carpeta_canvas(monkeypatch):
    monkeypatch.setattr(service.repository, "canvases_with_table", AsyncMock(return_value=[
        {"id": "sa1", "name": "Canvas B", "folderId": "f1", "projectId": "p1", "tableIds": ["t1"]},
        {"id": "sa2", "name": "Canvas A", "folderId": None, "projectId": "p1", "tableIds": ["t1"]},
    ]))
    monkeypatch.setattr(service.repository, "names_by_ids",
                        _names_stub({"f1": "Carpeta"}, {"p1": "Proyecto"}))
    out = asyncio.run(service.table_usage("t1"))
    assert out["total"] == 2
    assert [r["canvas"] for r in out["usage"]] == ["Canvas A", "Canvas B"]
    row = out["usage"][1]
    assert row["folder"] == "Carpeta" and row["project"] == "Proyecto" and row["canvasId"] == "sa1"


def test_table_usage_respeta_overlay_del_draft(monkeypatch):
    # El draft BORRA sa1 y AGREGA sa9 (canvas nuevo que referencia la tabla):
    # el uso efectivo muestra solo el nuevo.
    monkeypatch.setattr(service.repository, "canvases_with_table", AsyncMock(return_value=[
        {"id": "sa1", "name": "Viejo", "folderId": None, "projectId": "p1", "tableIds": ["t1"]},
    ]))
    monkeypatch.setattr(service.repository, "names_by_ids", _names_stub({}, {"p1": "Proyecto"}))
    monkeypatch.setattr(cs_repo, "changes_map", AsyncMock(return_value={"subject_areas": {
        "sa1": {"op": "delete"},
        "sa9": {"op": "upsert", "payload": {"name": "Nuevo", "projectId": "p1", "tableIds": ["t1", "t2"]}},
    }}))
    out = asyncio.run(service.table_usage("t1", "cs1"))
    assert out["total"] == 1
    assert out["usage"][0]["canvas"] == "Nuevo" and out["usage"][0]["canvasId"] == "sa9"


def test_table_usage_nombres_de_estructura_del_draft(monkeypatch):
    """Canvas en carpeta/proyecto CREADOS en el draft (aún no publicados): los
    nombres salen del payload del changeset — antes la ruta era '— › root'."""
    monkeypatch.setattr(service.repository, "canvases_with_table", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "names_by_ids", _names_stub({}, {}))
    monkeypatch.setattr(cs_repo, "changes_map", AsyncMock(return_value={
        "subject_areas": {"sa9": {"op": "upsert", "payload": {
            "name": "canvas001", "folderId": "f9", "projectId": "p9", "tableIds": ["t1"]}}},
        "folders": {"f9": {"op": "upsert", "payload": {"name": "folder001", "projectId": "p9"}}},
        "projects": {"p9": {"op": "upsert", "payload": {"name": "test_001"}}},
    }))
    out = asyncio.run(service.table_usage("t1", "cs1"))
    assert out["total"] == 1
    row = out["usage"][0]
    assert row["project"] == "test_001" and row["folder"] == "folder001" and row["canvas"] == "canvas001"


def test_table_usage_overlay_filtra_membresia_quitada(monkeypatch):
    # El draft saca t1 del canvas (upsert con tableIds SIN t1) → deja de contar.
    monkeypatch.setattr(service.repository, "canvases_with_table", AsyncMock(return_value=[
        {"id": "sa1", "name": "Canvas", "folderId": None, "projectId": "p1", "tableIds": ["t1"]},
    ]))
    monkeypatch.setattr(service.repository, "names_by_ids", _names_stub({}, {}))
    monkeypatch.setattr(cs_repo, "changes_map", AsyncMock(return_value={"subject_areas": {
        "sa1": {"op": "upsert", "payload": {"name": "Canvas", "projectId": "p1", "tableIds": ["t9"]}},
    }}))
    out = asyncio.run(service.table_usage("t1", "cs1"))
    assert out["total"] == 0
