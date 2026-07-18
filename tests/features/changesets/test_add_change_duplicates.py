"""`add_change` con control de duplicados server-side (spec 10 §9) — repository
mockeado (mismo patrón que test_effective_search)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.features.changesets import service
from app.features.changesets.validation import DuplicateEntityError


def _mock_repo(monkeypatch, published, changes_map):
    monkeypatch.setattr(service.repository, "get",
                        AsyncMock(return_value={"id": "c1", "status": "draft", "owner": "ana"}))
    monkeypatch.setattr(service.repository, "published", AsyncMock(return_value=published))
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value=changes_map))
    set_change = AsyncMock(return_value={"id": "c1", "status": "draft"})
    monkeypatch.setattr(service.repository, "set_change", set_change)
    return set_change


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
