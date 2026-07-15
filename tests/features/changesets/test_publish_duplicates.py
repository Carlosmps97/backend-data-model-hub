"""Re-chequeo de unicidad en el publish (spec 10 §9): cubre la carrera entre
changesets concurrentes — otro publish pudo crear el nombre DESPUÉS de que este
changeset pasó el chequeo de add_change. Repository mockeado."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.features.changesets import service
from app.features.changesets.validation import DuplicateEntityError


def _pub_by_collection(monkeypatch, data: dict[str, list[dict]]):
    async def _pub(collection, flt=None, **kwargs):
        return data.get(collection, [])
    monkeypatch.setattr(service.repository, "published", _pub)


def test_publish_duplicates_detecta_conflicto_con_publicado(monkeypatch):
    _pub_by_collection(monkeypatch, {
        "canonical_tables": [{"id": "tx", "physicalName": "CLIENTE", "logicalName": "c", "schema": "core"}],
    })
    changes = {"canonical_tables": {"t9": {"op": "upsert", "payload": {
        "physicalName": "cliente", "logicalName": "n", "schema": "CORE"}}}}
    errors = asyncio.run(service._publish_duplicates(changes))
    assert errors == ["Ya existe la tabla CORE.cliente"]


def test_publish_duplicates_sin_conflictos(monkeypatch):
    _pub_by_collection(monkeypatch, {})
    changes = {
        "canonical_tables": {"t9": {"op": "upsert", "payload": {
            "physicalName": "NUEVA", "logicalName": "n", "schema": "core"}}},
        "canonical_columns": {"c9": {"op": "upsert", "payload": {
            "tableId": "t9", "physicalName": "ID", "logicalName": "id", "dataType": "BIGINT"}}},
    }
    assert asyncio.run(service._publish_duplicates(changes)) == []


def test_publish_duplicates_columna_contra_publicado(monkeypatch):
    _pub_by_collection(monkeypatch, {
        "canonical_columns": [{"id": "c1", "tableId": "t1", "physicalName": "ID_CTA",
                               "logicalName": "id", "dataType": "BIGINT"}],
    })
    changes = {"canonical_columns": {"c9": {"op": "upsert", "payload": {
        "tableId": "t1", "physicalName": "id_cta", "logicalName": "x", "dataType": "STRING"}}}}
    assert asyncio.run(service._publish_duplicates(changes)) == [
        "Ya existe la columna id_cta en esta tabla"]


def test_apply_and_finalize_revierte_claim_ante_duplicados(monkeypatch):
    transitions: list[tuple[str, str | None]] = []

    async def fake_transition(cs_id, from_status, fields, expect=None):
        transitions.append((from_status, fields.get("status")))
        return {"id": cs_id, **fields}

    monkeypatch.setattr(service.repository, "transition", fake_transition)
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value={
        "canonical_tables": {"t9": {"op": "upsert", "payload": {
            "physicalName": "cta", "logicalName": "cuenta", "schema": "CORE"}}},
    }))
    _pub_by_collection(monkeypatch, {
        "canonical_tables": [{"id": "t1", "physicalName": "CTA", "logicalName": "cuenta", "schema": "core"}],
    })
    apply_mock = AsyncMock()
    monkeypatch.setattr(service.repository, "apply_changes", apply_mock)

    with pytest.raises(DuplicateEntityError):
        asyncio.run(service._apply_and_finalize(
            "c1", {"status": "approved"}, submitted_at="2026-01-01T00:00:00+00:00"))

    assert ("submitted", "approved") in transitions   # claim
    assert ("approved", "submitted") in transitions   # revert (como el gate 422)
    apply_mock.assert_not_called()                    # producción intacta
