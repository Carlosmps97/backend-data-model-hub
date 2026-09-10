"""Normalización del CASE del físico de COLUMNAS en el choke point de escritura
(doc 83, pedido owner 2026-09-10).

La regla `naming_config[column].case` sólo se aplicaba al DERIVAR el físico
desde el lógico (motor `physicalize`): un físico tipeado a mano (popup New
column en modo Physical, panel Properties) entraba tal cual — «monto_deuda» en
minúscula en un proyecto con regla `upper`. Ahora `add_change` /
`add_changes_bulk` aplican el case del scope al `physicalName` de TODO upsert
de `canonical_columns` (panel, popup, CTAS, paste, subcategorías, bulk upload)
ANTES de los chequeos de unicidad/longitud y del estampado del override.
Tablas quedan fuera (alcance del pedido). Con las reglas inaccesibles el
payload pasa tal cual: la normalización JAMÁS bloquea la escritura.

Servicio con repository mockeado (patrón test_physical_override_stamp).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.features.changesets import service

CS = {"projectId": "p1", "id": "c1", "status": "draft", "owner": "ana"}


def _mock_repo(monkeypatch, published=None, changes=None):
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value=CS))
    monkeypatch.setattr(service.repository, "published", published or AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_cross_project_check", AsyncMock())
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value=changes or {}))
    set_change = AsyncMock(return_value={"id": "c1", "status": "draft"})
    monkeypatch.setattr(service.repository, "set_change", set_change)
    set_bulk = AsyncMock(return_value={"id": "c1", "status": "draft"})
    monkeypatch.setattr(service.repository, "set_changes_bulk", set_bulk)
    monkeypatch.setattr(service.settings_service, "get_naming_for",
                        AsyncMock(return_value={"separator": "_", "case": "upper", "maxLength": 150}))
    return set_change, set_bulk


def _rules(monkeypatch, case="upper", mappings=None):
    return_value = (mappings if mappings is not None else {"monto": "MTO"}, "_", case)
    rules = AsyncMock(return_value=return_value)
    monkeypatch.setattr(service.dict_svc, "naming_rules", rules)
    return rules


def _col_payload(physical: str, logical: str = "monto", **extra) -> dict:
    return {"tableId": "t1", "physicalName": physical, "logicalName": logical,
            "dataType": "BIGINT", "ordinal": 0, **extra}


def _col(eid: str, physical: str, logical: str = "monto", **extra) -> dict:
    return {"collection": "canonical_columns", "entityId": eid, "op": "upsert",
            "payload": _col_payload(physical, logical, **extra)}


def _add_col(physical: str, logical: str = "monto", eid: str = "col1"):
    return asyncio.run(service.add_change(
        "c1", "ana", "canonical_columns", eid, "upsert", _col_payload(physical, logical)))


def test_add_change_column_lowercase_physical_becomes_upper(monkeypatch):
    set_change, _ = _mock_repo(monkeypatch)
    _rules(monkeypatch)
    _add_col("monto_deuda")
    payload = set_change.await_args.args[4]
    assert payload["physicalName"] == "MONTO_DEUDA"
    assert payload["logicalName"] == "monto"           # el LÓGICO no se toca


def test_add_change_column_follows_scope_case_lower(monkeypatch):
    set_change, _ = _mock_repo(monkeypatch)
    _rules(monkeypatch, case="lower")
    _add_col("MTO_DEU")
    assert set_change.await_args.args[4]["physicalName"] == "mto_deu"


def test_normalized_physical_feeds_override_stamp(monkeypatch):
    # «mto» tipeado ⇒ «MTO» == derivado de «monto» ⇒ NO es override.
    set_change, _ = _mock_repo(monkeypatch)
    _rules(monkeypatch)
    _add_col("mto")
    payload = set_change.await_args.args[4]
    assert payload["physicalName"] == "MTO"
    assert payload["physicalNameOverridden"] is False


def test_add_change_table_physical_untouched(monkeypatch):
    # Alcance del pedido: columnas. El físico de TABLA sigue entrando tal cual.
    set_change, _ = _mock_repo(monkeypatch)
    _rules(monkeypatch)
    asyncio.run(service.add_change(
        "c1", "ana", "canonical_tables", "t1", "upsert",
        {"physicalName": "hd_monto", "logicalName": "monto"}))
    assert set_change.await_args.args[4]["physicalName"] == "hd_monto"


def test_add_change_without_rules_keeps_payload(monkeypatch):
    set_change, _ = _mock_repo(monkeypatch)
    monkeypatch.setattr(service.dict_svc, "naming_rules",
                        AsyncMock(side_effect=RuntimeError("sin BD")))
    _add_col("monto_deuda")
    payload = set_change.await_args.args[4]
    assert payload["physicalName"] == "monto_deuda"
    assert "physicalNameOverridden" not in payload


def test_normalization_runs_before_duplicate_check(monkeypatch):
    # Pendiente «MTO_X» en el changeset + upsert «mto_x»: el 409 se evalúa (y
    # se reporta) con el nombre YA normalizado.
    pending = {"canonical_columns": {
        "col0": {"op": "upsert", "payload": _col_payload("MTO_X")}}}
    _mock_repo(monkeypatch, changes=pending)
    _rules(monkeypatch)
    with pytest.raises(service.DuplicateEntityError) as exc:
        _add_col("mto_x")
    assert "MTO_X" in str(exc.value)


def test_length_grandfather_is_case_insensitive(monkeypatch):
    # Columna legacy en minúscula y más larga que el límite: una edición NO
    # relacionada (p.ej. nullable) manda el doc completo y el físico se
    # normaliza a mayúscula ⇒ «mismo nombre salvo case» = heredado, no rename.
    long_lower = "c" * 40
    published = AsyncMock(side_effect=lambda coll, flt: (
        [{"_id": "col1", "id": "col1", "tableId": "t1", "physicalName": long_lower}]
        if flt.get("_id") == "col1" else []))
    set_change, _ = _mock_repo(monkeypatch, published=published)
    _rules(monkeypatch)
    monkeypatch.setattr(service.settings_service, "get_naming_for",
                        AsyncMock(return_value={"separator": "_", "case": "upper", "maxLength": 20}))
    _add_col(long_lower)
    assert set_change.await_args.args[4]["physicalName"] == long_lower.upper()


def test_bulk_normalizes_columns_once_per_scope(monkeypatch):
    _, set_bulk = _mock_repo(monkeypatch)
    rules = _rules(monkeypatch)
    items = [
        _col("col1", "monto_deuda"),                       # custom ⇒ MAYÚSCULA + override
        _col("col2", "mto"),                               # == derivado ⇒ sin override
        {"collection": "canonical_tables", "entityId": "t1", "op": "upsert",
         "payload": {"physicalName": "hd_monto", "logicalName": "monto"}},
    ]
    asyncio.run(service.add_changes_bulk("c1", "ana", items))
    sent = {i["entityId"]: i for i in set_bulk.await_args.args[1]}
    assert sent["col1"]["payload"]["physicalName"] == "MONTO_DEUDA"
    assert sent["col1"]["payload"]["physicalNameOverridden"] is True
    assert sent["col2"]["payload"]["physicalName"] == "MTO"
    assert sent["col2"]["payload"]["physicalNameOverridden"] is False
    assert sent["t1"]["payload"]["physicalName"] == "hd_monto"   # tablas: fuera de alcance
    assert rules.await_count == 2                                # una carga por scope


def test_bulk_duplicate_check_sees_normalized_names(monkeypatch):
    _mock_repo(monkeypatch)
    _rules(monkeypatch)
    with pytest.raises(service.DuplicateEntityError) as exc:
        asyncio.run(service.add_changes_bulk(
            "c1", "ana", [_col("col1", "MTO_X"), _col("col2", "mto_x")]))
    assert "MTO_X" in str(exc.value)
