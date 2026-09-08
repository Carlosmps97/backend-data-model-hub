"""Loader del contexto de la carga masiva (doc 55): estado EFECTIVO del
changeset (publicado + overlay) por colección, estándares vivos por scope y
columnas SOLO de las tablas referenciadas. Repositories mockeados."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.bulk_upload import loader


def _cs_mocks(monkeypatch, published: dict[str, list[dict]], changes: dict[str, dict]):
    """`published(coll, flt, projection)` por colección; `changes_map` por colección."""
    pub_calls: list[tuple] = []

    async def _published(collection, flt=None, limit=None, sort_field=None, projection=None):
        pub_calls.append((collection, flt, projection))
        return [dict(d) for d in published.get(collection, [])]

    async def _changes_map(cs_id, collections=None):
        return {c: dict(changes.get(c, {})) for c in (collections or changes)}

    monkeypatch.setattr(loader.cs_repo, "published", _published)
    monkeypatch.setattr(loader.cs_repo, "changes_map", _changes_map)
    return pub_calls


def _standards_mocks(monkeypatch):
    monkeypatch.setattr(loader.udp_repo, "list_udp", AsyncMock(return_value=[{"id": "u1", "name": "Universal", "level": "table"}]))
    monkeypatch.setattr(loader.domains_repo, "list_domains", AsyncMock(return_value=[{"id": "d1", "name": "Codigo", "defaultDataType": "STRING"}]))
    monkeypatch.setattr(loader.glossary_repo, "list_entries",
                        AsyncMock(side_effect=lambda pid, scope=None: [{"term": "codigo", "abbrev": "COD", "scope": scope}]))
    monkeypatch.setattr(loader.settings_service, "get_naming_for",
                        AsyncMock(side_effect=lambda pid, scope: {"scope": scope, "separator": "", "case": "upper", "maxLength": 150}))
    monkeypatch.setattr(loader.cs_repo, "get", AsyncMock(return_value={"id": "c1", "projectId": "p1"}))
    monkeypatch.setattr(loader.projects_repo, "get_project", AsyncMock(return_value={"id": "p1", "name": "P"}))


def test_load_context_aplica_overlay_del_draft(monkeypatch):
    _standards_mocks(monkeypatch)
    _cs_mocks(monkeypatch,
              published={"canonical_tables": [{"id": "t1", "projectId": "p1", "physicalName": "A", "logicalName": "a"},
                                              {"id": "t3", "projectId": "p1", "physicalName": "C", "logicalName": "c"}]},
              changes={"canonical_tables": {
                  "t1": {"op": "upsert", "payload": {"physicalName": "A2", "logicalName": "a"}},
                  "t2": {"op": "upsert", "payload": {"physicalName": "B", "logicalName": "b"}},
                  "t3": {"op": "delete"}}})
    ctx = asyncio.run(loader.load_context("c1"))
    by_id = {t["id"]: t for t in ctx.tables}
    assert set(by_id) == {"t1", "t2"}
    assert by_id["t1"]["physicalName"] == "A2"
    assert ctx.project_id == "p1" and ctx.project_name == "P"
    assert ctx.columns_by_table == {}


def test_load_context_proyecta_las_tablas_y_carga_estandares_por_scope(monkeypatch):
    _standards_mocks(monkeypatch)
    calls = _cs_mocks(monkeypatch, published={}, changes={})
    ctx = asyncio.run(loader.load_context("c1"))
    tables_call = next(c for c in calls if c[0] == "canonical_tables")
    assert tables_call[2] == loader.TABLE_PROJECTION
    assert tables_call[1] == {"projectId": "p1"}                 # doc 75: alcance por proyecto
    assert {c[0] for c in calls} == {"folders", "subject_areas", "schemas", "canonical_tables"}
    assert all(c[1] == {"projectId": "p1"} for c in calls)
    assert ctx.udp_defs[0]["name"] == "Universal"
    assert ctx.domains[0]["name"] == "Codigo"
    assert ctx.naming["table"]["maxLength"] == 150 and ctx.naming["column"]["scope"] == "column"
    assert ctx.glossary == {"table": {"codigo": "COD"}, "column": {"codigo": "COD"}}


def test_load_columns_solo_de_las_tablas_pedidas_con_overlay(monkeypatch):
    calls = _cs_mocks(monkeypatch,
                      published={"canonical_columns": [
                          {"id": "c1", "projectId": "p1", "tableId": "t1", "physicalName": "X", "ordinal": 0},
                          {"id": "c2", "projectId": "p1", "tableId": "t1", "physicalName": "Y", "ordinal": 1}]},
                      changes={"canonical_columns": {
                          "c2": {"op": "delete"},
                          "c9": {"op": "upsert", "payload": {"tableId": "t1", "physicalName": "Z", "ordinal": 2}},
                          "c8": {"op": "upsert", "payload": {"tableId": "t7", "physicalName": "W", "ordinal": 0}}}})
    out = asyncio.run(loader.load_columns("c1", ["t1"]))
    assert sorted(c["id"] for c in out["t1"]) == ["c1", "c9"]
    assert "t7" not in out
    (call,) = calls
    assert call[0] == "canonical_columns" and call[1] == {"tableId": {"$in": ["t1"]}}


def test_load_columns_sin_ids_no_consulta(monkeypatch):
    calls = _cs_mocks(monkeypatch, published={}, changes={})
    assert asyncio.run(loader.load_columns("c1", [])) == {}
    assert calls == []
