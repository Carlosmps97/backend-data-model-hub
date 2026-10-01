"""Versionado de la entidad `schemas` (doc 18 §3): colección en VERSIONED,
payloads validados, unicidad de `name` en add_change/publish, guard de
delete-en-uso al publicar, y rename con propagación a tablas/vistas del draft.
Repository mockeado (patrón test_publish_duplicates.py)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.features.changesets import service
from app.features.changesets.repository import VERSIONED
from app.features.changesets.validation import (
    DuplicateEntityError,
    SchemaInUseError,
    duplicate_error,
    payload_error,
)


# ── Colección versionada + validación de payload ──────────────────────────


def test_schemas_esta_en_versioned_antes_de_tablas():
    assert "schemas" in VERSIONED
    # orden de dependencia del apply: el esquema existe antes que sus tablas
    assert VERSIONED.index("schemas") < VERSIONED.index("canonical_tables")


def test_payload_schema_valido_pasa():
    assert payload_error("schemas", "s1", "upsert", {"projectId": "p1", "name": "core"}) is None


def test_payload_schema_sin_name_falla():
    err = payload_error("schemas", "s1", "upsert", {"description": "x"})
    assert err is not None and "name" in err


# ── Unicidad de `name` (add_change + publish) ──────────────────────────────


def test_duplicate_error_schemas_case_insensitive():
    published = [{"id": "s1", "name": "core"}]
    err = duplicate_error("schemas", "s9", {"name": "CORE"}, published, {})
    assert err == "Schema CORE already exists"


def test_duplicate_error_schemas_delete_pendiente_libera_el_nombre():
    published = [{"id": "s1", "name": "core"}]
    pending = {"s1": {"op": "delete"}}
    assert duplicate_error("schemas", "s9", {"name": "core"}, published, pending) is None


def test_publish_duplicates_detecta_esquema_duplicado(monkeypatch):
    async def _pub(collection, flt=None, **kwargs):
        return {"schemas": [{"id": "s1", "name": "core"}]}.get(collection, [])
    monkeypatch.setattr(service.repository, "published", _pub)
    changes = {"schemas": {"s9": {"op": "upsert", "payload": {"name": "CORE"}}}}
    assert asyncio.run(service._publish_duplicates("p1", changes)) == ["Schema CORE already exists"]


# ── Overlay por esquema (puro) + guard de delete-en-uso ───────────────────


def test_overlay_by_schema_aplica_deletes_y_upserts():
    published = [
        {"id": "t1", "schema": "core", "physicalName": "A"},
        {"id": "t2", "schema": "core", "physicalName": "B"},
    ]
    coll_changes = {
        "t2": {"op": "delete"},                                     # sale
        "t3": {"op": "upsert", "payload": {"schema": "core", "physicalName": "C"}},   # entra
        "t1": {"op": "upsert", "payload": {"schema": "otro", "physicalName": "A"}},   # se muda
    }
    out = service._overlay_by_schema(published, coll_changes, "core")
    assert [d["id"] for d in out] == ["t3"]


def test_publish_schema_deletes_detecta_uso(monkeypatch):
    async def _pub(collection, flt=None, **kwargs):
        return {
            "schemas": [{"id": "s1", "name": "core"}],
            "canonical_tables": [{"id": "t1", "schema": "core", "physicalName": "A",
                                  "logicalName": "a"}],
            "views": [],
        }.get(collection, [])
    monkeypatch.setattr(service.repository, "published", _pub)
    changes = {"schemas": {"s1": {"op": "delete"}}}
    errors = asyncio.run(service._publish_schema_deletes("p1", changes))
    assert len(errors) == 1 and "core" in errors[0]


def test_publish_schema_deletes_ok_si_el_draft_vacia_el_esquema(monkeypatch):
    # el MISMO draft borra la única tabla del esquema → el delete es válido
    async def _pub(collection, flt=None, **kwargs):
        return {
            "schemas": [{"id": "s1", "name": "core"}],
            "canonical_tables": [{"id": "t1", "schema": "core", "physicalName": "A",
                                  "logicalName": "a"}],
            "views": [],
        }.get(collection, [])
    monkeypatch.setattr(service.repository, "published", _pub)
    changes = {
        "schemas": {"s1": {"op": "delete"}},
        "canonical_tables": {"t1": {"op": "delete"}},
    }
    assert asyncio.run(service._publish_schema_deletes("p1", changes)) == []


def test_apply_and_finalize_revierte_claim_si_esquema_en_uso(monkeypatch):
    transitions: list[tuple[str, str | None]] = []

    async def fake_transition(cs_id, from_status, fields, expect=None):
        transitions.append((from_status, fields.get("status")))
        return {"id": cs_id, **fields}

    monkeypatch.setattr(service.repository, "transition", fake_transition)
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value={
        "schemas": {"s1": {"op": "delete"}},
    }))

    async def _pub(collection, flt=None, **kwargs):
        return {
            "schemas": [{"id": "s1", "name": "core"}],
            "canonical_tables": [{"id": "t1", "schema": "core", "physicalName": "A",
                                  "logicalName": "a"}],
            "views": [],
        }.get(collection, [])
    monkeypatch.setattr(service.repository, "published", _pub)
    apply_mock = AsyncMock()
    monkeypatch.setattr(service.repository, "apply_changes", apply_mock)

    with pytest.raises(SchemaInUseError):
        asyncio.run(service._apply_and_finalize(
            {"id": "c1", "projectId": "p1"}, {"status": "approved"}, submitted_at="2026-01-01T00:00:00+00:00"))

    assert ("submitted", "approved") in transitions   # claim
    assert ("approved", "submitted") in transitions   # revert
    apply_mock.assert_not_called()                    # producción intacta


# ── Rename con propagación (server-side, dentro del draft) ────────────────


def _mock_rename_env(monkeypatch, *, tables: list[dict], views: list[dict],
                     changes: dict | None = None):
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value={
        "id": "c1", "projectId": "p1", "status": "draft", "owner": "u1"}))
    monkeypatch.setattr(service, "effective", AsyncMock(return_value=[
        {"id": "s1", "name": "core", "description": None}]))
    recorded: list[tuple] = []

    async def fake_add_changes_bulk(cs_id, actor, items):
        # Doc 105 (H1): el rename graba TODO en un solo lote (todo o nada).
        recorded.extend((i["collection"], i["entityId"], i["op"], i["payload"]) for i in items)
        return {"id": cs_id}

    monkeypatch.setattr(service, "add_changes_bulk", fake_add_changes_bulk)

    async def _pub(collection, flt=None, **kwargs):
        return {"canonical_tables": tables, "views": views}.get(collection, [])

    monkeypatch.setattr(service.repository, "published", _pub)
    monkeypatch.setattr(service.repository, "changes_map",
                        AsyncMock(return_value=changes or {}))
    return recorded


def test_rename_schema_propaga_a_tablas_y_vistas(monkeypatch):
    recorded = _mock_rename_env(
        monkeypatch,
        tables=[{"id": "t1", "schema": "core", "physicalName": "A", "logicalName": "a"},
                {"id": "t2", "schema": "otro", "physicalName": "B", "logicalName": "b"}],
        views=[{"id": "v1", "schema": "core", "name": "v_a"}],
    )
    out = asyncio.run(service.rename_schema("c1", "u1", "s1", "core_v2"))
    assert out == {"tables": 1, "views": 1}
    # upsert del schema con el nombre nuevo + doc COMPLETO por tabla/vista
    assert ("schemas", "s1", "upsert", {"name": "core_v2", "description": None}) in recorded
    t = next(r for r in recorded if r[0] == "canonical_tables")
    assert t[1] == "t1" and t[3]["schema"] == "core_v2" and t[3]["physicalName"] == "A"
    v = next(r for r in recorded if r[0] == "views")
    assert v[1] == "v1" and v[3]["schema"] == "core_v2"


def test_rename_schema_nombre_invalido_422(monkeypatch):
    from app.features.changesets.validation import InvalidPayloadError
    with pytest.raises(InvalidPayloadError):
        asyncio.run(service.rename_schema("c1", "u1", "s1", "mal nombre"))


def test_rename_schema_inexistente_devuelve_none(monkeypatch):
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value={"id": "c1", "projectId": "p1", "status": "draft", "owner": "u1"}))
    monkeypatch.setattr(service, "effective", AsyncMock(return_value=[]))
    assert asyncio.run(service.rename_schema("c1", "u1", "sX", "core_v2")) is None


# ── Impacto de tocar un esquema (doc 18 v2) ────────────────────────────────


def test_schema_impact_cuenta_tablas_vistas_y_canvases(monkeypatch):
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value={"id": "c1", "projectId": "p1", "status": "draft", "owner": "u1"}))
    monkeypatch.setattr(service, "effective", AsyncMock(return_value=[
        {"id": "s1", "name": "core", "description": None}]))

    async def _pub(collection, flt=None, **kwargs):
        return {
            "canonical_tables": [
                {"id": "t1", "schema": "core", "physicalName": "A", "logicalName": "a"},
                {"id": "t2", "schema": "core", "physicalName": "B", "logicalName": "b"},
            ],
            "views": [{"id": "v1", "schema": "core", "name": "v_a",
                       "sourceTableIds": ["t9"]}],
            "subject_areas": [
                {"id": "sa1", "name": "c1", "tableIds": ["t1", "x"]},   # por tabla
                {"id": "sa2", "name": "c2", "tableIds": ["t9"]},        # por fuente de vista
                {"id": "sa3", "name": "c3", "tableIds": ["zz"]},        # no afectado
            ],
        }.get(collection, [])

    monkeypatch.setattr(service.repository, "published", _pub)
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value={}))
    out = asyncio.run(service.schema_impact("c1", "s1"))
    assert out == {"name": "core", "tables": 2, "views": 1, "canvases": 2}


def test_schema_impact_esquema_inexistente_none(monkeypatch):
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value={"id": "c1", "projectId": "p1", "status": "draft", "owner": "u1"}))
    monkeypatch.setattr(service, "effective", AsyncMock(return_value=[]))
    assert asyncio.run(service.schema_impact("c1", "sX")) is None


def test_schema_impact_respeta_overlay_del_draft(monkeypatch):
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value={"id": "c1", "projectId": "p1", "status": "draft", "owner": "u1"}))
    # el draft borra t1 → el impacto NO la cuenta (ni a su canvas)
    monkeypatch.setattr(service, "effective", AsyncMock(return_value=[
        {"id": "s1", "name": "core", "description": None}]))

    async def _pub(collection, flt=None, **kwargs):
        return {
            "canonical_tables": [{"id": "t1", "schema": "core", "physicalName": "A",
                                  "logicalName": "a"}],
            "views": [],
            "subject_areas": [{"id": "sa1", "name": "c1", "tableIds": ["t1"]}],
        }.get(collection, [])

    monkeypatch.setattr(service.repository, "published", _pub)
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value={
        "canonical_tables": {"t1": {"op": "delete"}},
    }))
    out = asyncio.run(service.schema_impact("c1", "s1"))
    assert out == {"name": "core", "tables": 0, "views": 0, "canvases": 0}


# ── Delete dentro del changeset ────────────────────────────────────────────


def test_delete_schema_in_changeset_en_uso(monkeypatch):
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value={"id": "c1", "projectId": "p1", "status": "draft", "owner": "u1"}))
    monkeypatch.setattr(service, "effective", AsyncMock(return_value=[
        {"id": "s1", "name": "core", "description": None}]))

    async def _pub(collection, flt=None, **kwargs):
        return {"canonical_tables": [{"id": "t1", "schema": "core", "physicalName": "A",
                                      "logicalName": "a"}],
                "views": []}.get(collection, [])
    monkeypatch.setattr(service.repository, "published", _pub)
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value={}))
    out = asyncio.run(service.delete_schema_in_changeset("c1", "u1", "s1"))
    assert out == ("in-use", 1)


def test_delete_schema_in_changeset_vacio_registra_delete(monkeypatch):
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value={"id": "c1", "projectId": "p1", "status": "draft", "owner": "u1"}))
    monkeypatch.setattr(service, "effective", AsyncMock(return_value=[
        {"id": "s1", "name": "core", "description": None}]))

    async def _pub(collection, flt=None, **kwargs):
        return []
    monkeypatch.setattr(service.repository, "published", _pub)
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value={}))
    add = AsyncMock(return_value={"id": "c1"})
    monkeypatch.setattr(service, "add_change", add)
    out = asyncio.run(service.delete_schema_in_changeset("c1", "u1", "s1"))
    assert out == {"id": "c1"}
    add.assert_awaited_once_with("c1", "u1", "schemas", "s1", "delete", None)


def test_rename_schema_conserva_kind(monkeypatch):
    # el upsert del rename espeja el doc EFECTIVO completo: kind (doc 44) viaja
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value={
        "id": "c1", "projectId": "p1", "status": "draft", "owner": "u1"}))
    monkeypatch.setattr(service, "effective", AsyncMock(return_value=[
        {"id": "s1", "name": "core_vu", "description": None, "kind": "views"}]))
    recorded: list[tuple] = []

    async def fake_add_changes_bulk(cs_id, actor, items):
        # Doc 105 (H1): el rename graba TODO en un solo lote (todo o nada).
        recorded.extend((i["collection"], i["entityId"], i["op"], i["payload"]) for i in items)
        return {"id": cs_id}

    monkeypatch.setattr(service, "add_changes_bulk", fake_add_changes_bulk)
    monkeypatch.setattr(service.repository, "published", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value={}))
    out = asyncio.run(service.rename_schema("c1", "u1", "s1", "core_v2_vu"))
    assert out == {"tables": 0, "views": 0}
    sch = next(r for r in recorded if r[0] == "schemas")
    assert sch[3]["name"] == "core_v2_vu" and sch[3]["kind"] == "views"
