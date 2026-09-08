"""Doc 75 I1/I2 — el servidor estampa projectId y rechaza referencias a otro proyecto."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.features.changesets import service
from app.features.changesets.validation import CrossProjectError, collect_refs, cross_project_error


def test_collect_refs_por_coleccion():
    assert collect_refs("canonical_columns", {"tableId": "t1"}) == {"canonical_tables": {"t1"}}
    assert collect_refs("relationships", {"parentTableId": "a", "childTableId": "b"}) == {"canonical_tables": {"a", "b"}}
    assert collect_refs("views", {"sourceTableIds": ["a", "b"]}) == {"canonical_tables": {"a", "b"}}
    assert collect_refs("subject_areas", {"tableIds": ["a"], "viewIds": ["v"], "folderId": "f"}) == {
        "canonical_tables": {"a"}, "views": {"v"}, "folders": {"f"}}
    assert collect_refs("folders", {"parentFolderId": None}) == {}
    assert collect_refs("canonical_tables", {"schema": "x"}) == {}


def test_cross_project_error_mensajes():
    owners = {"canonical_tables": {"a": "p1", "b": "p2", "c": None}}
    assert cross_project_error("p1", {"canonical_tables": {"a"}}, owners) is None
    assert "belongs to another project" in cross_project_error("p1", {"canonical_tables": {"b"}}, owners)
    assert "doesn't exist in this project" in cross_project_error("p1", {"canonical_tables": {"c"}}, owners)


def _cs(monkeypatch, project="p1"):
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value={"projectId": "p1", "id": "cs1", "owner": "ana", "status": "draft", "projectId": project}))
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value={}))
    monkeypatch.setattr(service.settings_service, "get_naming_for", AsyncMock(return_value={"maxLength": 150}))
    monkeypatch.setattr(service.dict_svc, "naming_rules", AsyncMock(return_value=({}, "", "upper")))


def test_add_change_estampa_project_id_del_changeset(monkeypatch):
    _cs(monkeypatch)
    monkeypatch.setattr(service.repository, "published", AsyncMock(return_value=[]))
    set_change = AsyncMock(return_value={"id": "cs1"})
    monkeypatch.setattr(service.repository, "set_change", set_change)
    payload = {"physicalName": "T", "logicalName": "t", "projectId": "OTRO"}
    asyncio.run(service.add_change("cs1", "ana", "canonical_tables", "t1", "upsert", payload))
    assert set_change.call_args.args[4]["projectId"] == "p1"


def test_add_change_columna_de_tabla_ajena_409(monkeypatch):
    _cs(monkeypatch)

    async def _pub(collection, flt=None, **kw):
        if collection == "canonical_tables":
            return [{"id": "tX", "projectId": "p2", "physicalName": "X", "logicalName": "x"}]
        return []
    monkeypatch.setattr(service.repository, "published", _pub)
    monkeypatch.setattr(service.repository, "set_change", AsyncMock())
    with pytest.raises(CrossProjectError):
        asyncio.run(service.add_change("cs1", "ana", "canonical_columns", "c1", "upsert",
                                       {"tableId": "tX", "physicalName": "C", "logicalName": "c", "dataType": "INT"}))
    service.repository.set_change.assert_not_awaited()


def test_bulk_acepta_referencia_a_tabla_del_mismo_lote(monkeypatch):
    _cs(monkeypatch)
    monkeypatch.setattr(service.repository, "published", AsyncMock(return_value=[]))
    bulk = AsyncMock(return_value={"id": "cs1"})
    monkeypatch.setattr(service.repository, "set_changes_bulk", bulk)
    items = [
        {"collection": "canonical_tables", "entityId": "tN", "op": "upsert",
         "payload": {"physicalName": "N", "logicalName": "n"}},
        {"collection": "canonical_columns", "entityId": "cN", "op": "upsert",
         "payload": {"tableId": "tN", "physicalName": "C", "logicalName": "c", "dataType": "INT"}},
    ]
    asyncio.run(service.add_changes_bulk("cs1", "ana", items))
    written = bulk.call_args.args[1]
    assert all(it["payload"]["projectId"] == "p1" for it in written)


def test_cambio_sobre_projects_solo_del_propio_proyecto():
    """Doc 75 D5: rename/description/delete sólo del PROPIO proyecto del draft."""
    cs = {"id": "cs1", "projectId": "p1"}
    with pytest.raises(CrossProjectError):
        asyncio.run(service._cross_project_check(cs, "projects", "p2", "delete", None, {}))
    asyncio.run(service._cross_project_check(cs, "projects", "p1", "delete", None, {}))
    asyncio.run(service._cross_project_check(cs, "projects", "p1", "upsert", {"name": "Nuevo"}, {}))
