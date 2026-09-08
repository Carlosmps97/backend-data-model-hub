"""Estampado del override de físico en el choke point de escritura (doc 68).

`add_change`/`add_changes_bulk` infieren `physicalNameOverridden` para upserts
de tablas/columnas: flag del payload OR (físico ≠ physicalize(lógico) con las
reglas del scope). Cubre TODOS los caminos del front (panels, CTAS, paste,
bulk upload) sin tocar cada feature. Con las reglas inaccesibles (repos
mockeados sin naming) el estampado hace fallback al flag del payload — jamás
bloquea la escritura.

Servicio con repository mockeado (patrón test_bulk_changes).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.changesets import service


def _mock_repo(monkeypatch):
    monkeypatch.setattr(service.repository, "get",
                        AsyncMock(return_value={"projectId": "p1", "id": "c1", "status": "draft", "owner": "ana"}))
    monkeypatch.setattr(service.repository, "published", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_cross_project_check", AsyncMock())
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value={}))
    set_change = AsyncMock(return_value={"id": "c1", "status": "draft"})
    monkeypatch.setattr(service.repository, "set_change", set_change)
    set_bulk = AsyncMock(return_value={"id": "c1", "status": "draft"})
    monkeypatch.setattr(service.repository, "set_changes_bulk", set_bulk)
    monkeypatch.setattr(service.settings_service, "get_naming_for",
                        AsyncMock(return_value={"separator": "", "case": "upper",
                                                "maxLength": 150}))
    return set_change, set_bulk


def _mock_rules(monkeypatch, mappings=None):
    # Reglas de naming del scope: mismas para table/column en estos tests.
    monkeypatch.setattr(service.dict_svc, "naming_rules",
                        AsyncMock(return_value=(mappings or {"monto": "MTO"}, "", "upper")))


def _table(eid: str, physical: str, logical: str = "monto", **extra) -> dict:
    return {"collection": "canonical_tables", "entityId": eid, "op": "upsert",
            "payload": {"physicalName": physical, "logicalName": logical, **extra}}


def test_add_change_stamps_custom_physical_as_override(monkeypatch):
    set_change, _ = _mock_repo(monkeypatch)
    _mock_rules(monkeypatch)
    asyncio.run(service.add_change(
        "c1", "ana", "canonical_tables", "t1", "upsert",
        {"physicalName": "HD_MONTO", "logicalName": "monto"}))
    payload = set_change.await_args.args[4]
    assert payload["physicalNameOverridden"] is True


def test_add_change_stamps_derived_physical_as_not_override(monkeypatch):
    set_change, _ = _mock_repo(monkeypatch)
    _mock_rules(monkeypatch)
    asyncio.run(service.add_change(
        "c1", "ana", "canonical_tables", "t1", "upsert",
        {"physicalName": "MTO", "logicalName": "monto"}))
    payload = set_change.await_args.args[4]
    assert payload["physicalNameOverridden"] is False


def test_add_change_keeps_explicit_true_even_if_name_matches(monkeypatch):
    set_change, _ = _mock_repo(monkeypatch)
    _mock_rules(monkeypatch)
    asyncio.run(service.add_change(
        "c1", "ana", "canonical_tables", "t1", "upsert",
        {"physicalName": "MTO", "logicalName": "monto",
         "physicalNameOverridden": True}))
    payload = set_change.await_args.args[4]
    assert payload["physicalNameOverridden"] is True


def test_add_change_without_rules_falls_back_to_payload_flag(monkeypatch):
    # Repos de naming inaccesibles (p.ej. unit tests de otras features): el
    # payload pasa tal cual — el estampado NUNCA bloquea la escritura.
    set_change, _ = _mock_repo(monkeypatch)
    monkeypatch.setattr(service.dict_svc, "naming_rules",
                        AsyncMock(side_effect=RuntimeError("sin BD")))
    asyncio.run(service.add_change(
        "c1", "ana", "canonical_tables", "t1", "upsert",
        {"physicalName": "HD_MONTO", "logicalName": "monto"}))
    payload = set_change.await_args.args[4]
    assert "physicalNameOverridden" not in payload


def test_add_change_ignores_deletes_and_other_collections(monkeypatch):
    set_change, _ = _mock_repo(monkeypatch)
    rules = AsyncMock(return_value=({}, "", "upper"))
    monkeypatch.setattr(service.dict_svc, "naming_rules", rules)
    asyncio.run(service.add_change("c1", "ana", "canonical_tables", "t1", "delete", None))
    asyncio.run(service.add_change(
        "c1", "ana", "schemas", "s1", "upsert", {"name": "ESQ"}))
    rules.assert_not_called()
    assert "physicalNameOverridden" not in (set_change.await_args.args[4] or {})


def test_bulk_stamps_each_scope_with_its_rules(monkeypatch):
    _, set_bulk = _mock_repo(monkeypatch)
    rules = AsyncMock(return_value=({"monto": "MTO"}, "", "upper"))
    monkeypatch.setattr(service.dict_svc, "naming_rules", rules)
    items = [
        _table("t1", "HD_MONTO"),                      # custom ⇒ True
        _table("t2", "MTO"),                           # derivado ⇒ False
        {"collection": "canonical_columns", "entityId": "col1", "op": "upsert",
         "payload": {"tableId": "t1", "physicalName": "MTO", "logicalName": "monto",
                     "dataType": "BIGINT", "ordinal": 0}},
        {"collection": "views", "entityId": "v1", "op": "upsert",
         "payload": {"name": "V_X", "sourceTableIds": ["t1"]}},
    ]
    asyncio.run(service.add_changes_bulk("c1", "ana", items))
    sent = {i["entityId"]: i for i in set_bulk.await_args.args[1]}
    assert sent["t1"]["payload"]["physicalNameOverridden"] is True
    assert sent["t2"]["payload"]["physicalNameOverridden"] is False
    assert sent["col1"]["payload"]["physicalNameOverridden"] is False
    assert "physicalNameOverridden" not in sent["v1"]["payload"]
    # Reglas cargadas UNA vez por scope presente en el lote (no por ítem).
    assert rules.await_count == 2
