"""`add_change` con control de duplicados server-side (spec 10 §9) — repository
mockeado (mismo patrón que test_effective_search)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.features.changesets import service
from app.features.changesets.validation import DuplicateEntityError, NameTooLongError


def _mock_repo(monkeypatch, published, changes_map, max_length=150):
    monkeypatch.setattr(service.repository, "get",
                        AsyncMock(return_value={"projectId": "p1", "id": "c1", "status": "draft", "owner": "ana"}))
    monkeypatch.setattr(service.repository, "published", AsyncMock(return_value=published))
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value=changes_map))
    set_change = AsyncMock(return_value={"id": "c1", "status": "draft"})
    monkeypatch.setattr(service.repository, "set_change", set_change)
    # Doc 75 I2: el guard anti-cruce tiene sus propios tests (test_cross_project_guard).
    monkeypatch.setattr(service, "_cross_project_check", AsyncMock())
    # add_change ahora consulta el naming_config (límite de caracteres del físico)
    # → se mockea para no tocar la BD.
    monkeypatch.setattr(service.settings_service, "get_naming_for",
                        AsyncMock(return_value={"maxLength": max_length}))
    return set_change


def test_upsert_nombre_muy_largo_bloquea(monkeypatch):
    set_change = _mock_repo(monkeypatch, published=[], changes_map={}, max_length=10)
    with pytest.raises(NameTooLongError, match="10-character"):
        asyncio.run(service.add_change(
            "c1", "ana", "canonical_tables", "t9", "upsert",
            {"physicalName": "NOMBREDEMASIADOLARGO", "logicalName": "x", "schema": "core"}))
    set_change.assert_not_called()


def test_upsert_nombre_largo_heredado_sin_cambio_pasa(monkeypatch):
    # el mismo nombre largo ya existe publicado con ESE id → grandfather (no bloquea).
    set_change = _mock_repo(
        monkeypatch,
        published=[{"id": "t9", "physicalName": "NOMBREDEMASIADOLARGO", "logicalName": "x", "schema": "core"}],
        changes_map={}, max_length=10)
    asyncio.run(service.add_change(
        "c1", "ana", "canonical_tables", "t9", "upsert",
        {"physicalName": "NOMBREDEMASIADOLARGO", "logicalName": "x", "schema": "core"}))
    set_change.assert_called_once()


def test_upsert_tabla_duplicada_levanta_duplicate(monkeypatch):
    set_change = _mock_repo(
        monkeypatch,
        published=[{"id": "t1", "physicalName": "CLIENTE", "logicalName": "cliente", "schema": "core"}],
        changes_map={},
    )
    with pytest.raises(DuplicateEntityError, match="already exists"):
        asyncio.run(service.add_change(
            "c1", "ana", "canonical_tables", "t9", "upsert",
            {"physicalName": "cliente", "logicalName": "otro", "schema": "CORE"}))
    set_change.assert_not_called()


def test_upsert_misma_tabla_pasa(monkeypatch):
    set_change = _mock_repo(
        monkeypatch,
        published=[{"id": "t1", "physicalName": "CLIENTE", "logicalName": "cliente", "schema": "core"}],
        changes_map={},
    )
    res = asyncio.run(service.add_change(
        "c1", "ana", "canonical_tables", "t1", "upsert",
        {"physicalName": "CLIENTE", "logicalName": "cliente renombrado", "schema": "core"}))
    assert isinstance(res, dict)
    set_change.assert_awaited_once()


def test_upsert_columna_duplicada_en_tabla(monkeypatch):
    set_change = _mock_repo(
        monkeypatch,
        published=[{"id": "c1", "tableId": "t1", "physicalName": "ID_CTA",
                    "logicalName": "id", "dataType": "BIGINT"}],
        changes_map={},
    )
    with pytest.raises(DuplicateEntityError, match="already exists in this table"):
        asyncio.run(service.add_change(
            "c1", "ana", "canonical_columns", "c9", "upsert",
            {"tableId": "t1", "physicalName": "id_cta", "logicalName": "otra",
             "dataType": "STRING", "ordinal": 1}))
    set_change.assert_not_called()


def test_upsert_cross_schema_conflicta(monkeypatch):
    # Doc 50: la unicidad del físico de tabla es GLOBAL — cambiar el esquema
    # del payload ya no libera el nombre.
    set_change = _mock_repo(
        monkeypatch,
        published=[{"id": "t1", "physicalName": "M_CLIENTE", "logicalName": "cliente", "schema": "A"}],
        changes_map={},
    )
    with pytest.raises(DuplicateEntityError, match=r"already exists \(schema A\)"):
        asyncio.run(service.add_change(
            "c1", "ana", "canonical_tables", "t9", "upsert",
            {"physicalName": "m_cliente", "logicalName": "otro", "schema": "B"}))
    set_change.assert_not_called()


def test_upsert_homonimo_legacy_sin_rename_pasa(monkeypatch):
    # Grandfather (doc 50): dos homónimos cross-schema YA publicados (data
    # migrada bajo la regla vieja). Editar uno SIN tocar el físico no bloquea;
    # el mock respeta el filtro por _id de la query del grandfather.
    docs = [
        {"id": "t1", "physicalName": "M_CLIENTE", "logicalName": "cliente", "schema": "A"},
        {"id": "t2", "physicalName": "M_CLIENTE", "logicalName": "cliente b", "schema": "B"},
    ]
    set_change = _mock_repo(monkeypatch, published=docs, changes_map={})

    async def _pub(collection, flt=None, **kwargs):
        if isinstance(flt, dict) and "_id" in flt:
            return [d for d in docs if d["id"] == flt["_id"]]
        return docs
    monkeypatch.setattr(service.repository, "published", _pub)
    res = asyncio.run(service.add_change(
        "c1", "ana", "canonical_tables", "t2", "upsert",
        {"physicalName": "M_CLIENTE", "logicalName": "cliente b editada", "schema": "B"}))
    assert isinstance(res, dict)
    set_change.assert_awaited_once()


def test_upsert_rename_hacia_nombre_tomado_bloquea(monkeypatch):
    # El grandfather NO cubre renames: mover t2 hacia el nombre de t1 bloquea.
    docs = [
        {"id": "t1", "physicalName": "M_CLIENTE", "logicalName": "cliente", "schema": "A"},
        {"id": "t2", "physicalName": "M_PERSONA", "logicalName": "persona", "schema": "B"},
    ]
    set_change = _mock_repo(monkeypatch, published=docs, changes_map={})

    async def _pub(collection, flt=None, **kwargs):
        if isinstance(flt, dict) and "_id" in flt:
            return [d for d in docs if d["id"] == flt["_id"]]
        return docs
    monkeypatch.setattr(service.repository, "published", _pub)
    with pytest.raises(DuplicateEntityError, match="already exists"):
        asyncio.run(service.add_change(
            "c1", "ana", "canonical_tables", "t2", "upsert",
            {"physicalName": "m_cliente", "logicalName": "persona", "schema": "B"}))
    set_change.assert_not_called()


def test_conflicto_contra_pendiente_del_mismo_changeset(monkeypatch):
    pending = {"canonical_tables": {"t8": {"op": "upsert", "payload": {
        "physicalName": "NUEVA", "logicalName": "nueva", "schema": "core"}}}}
    set_change = _mock_repo(monkeypatch, published=[], changes_map=pending)
    with pytest.raises(DuplicateEntityError):
        asyncio.run(service.add_change(
            "c1", "ana", "canonical_tables", "t9", "upsert",
            {"physicalName": "nueva", "logicalName": "x", "schema": "CORE"}))
    set_change.assert_not_called()


def test_delete_no_chequea_duplicados(monkeypatch):
    set_change = _mock_repo(monkeypatch, published=[], changes_map={})
    res = asyncio.run(service.add_change("c1", "ana", "canonical_columns", "c1", "delete", None))
    assert isinstance(res, dict)
    set_change.assert_awaited_once()
