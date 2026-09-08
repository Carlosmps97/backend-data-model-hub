"""Guard N=N (doc 47): todo upsert de `relationships` migra la llave COMPLETA
del padre. Parte pura acá; la integración con service más abajo, con el
repository mockeado — mismo patrón que test_add_change_duplicates."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.features.changesets import service
from app.features.changesets.validation import (
    RelationshipKeyMismatchError,
    relationship_key_error,
)


def _rel(pairs):
    return {"parentTableId": "P", "childTableId": "H", "pairs": pairs,
            "parentCardinality": "one", "childCardinality": "zero-many", "identifying": True}


def test_n_igual_n_pasa():
    ok = _rel([{"parentColumnId": "a", "childColumnId": "x"},
               {"parentColumnId": "b", "childColumnId": "y"}])
    assert relationship_key_error(ok, {"a", "b"}) is None


def test_subconjunto_y_sobra_bloquean():
    r = _rel([{"parentColumnId": "a", "childColumnId": "x"}])
    assert "full primary key" in relationship_key_error(r, {"a", "b"})
    assert "full primary key" in relationship_key_error(_rel(
        [{"parentColumnId": "a", "childColumnId": "x"},
         {"parentColumnId": "z", "childColumnId": "y"}]), {"a"})


def test_padre_repetido_en_pares_bloquea():
    r = _rel([{"parentColumnId": "a", "childColumnId": "x"},
              {"parentColumnId": "a", "childColumnId": "y"}])
    assert "repeats a parent key column" in relationship_key_error(r, {"a"})


def test_hijo_repetido_bloquea():
    r = _rel([{"parentColumnId": "a", "childColumnId": "x"},
              {"parentColumnId": "b", "childColumnId": "x"}])
    assert "same child column" in relationship_key_error(r, {"a", "b"})


def test_automapeo_bloquea():
    r = _rel([{"parentColumnId": "a", "childColumnId": "a"}])
    assert "itself" in relationship_key_error(r, {"a"})


# ── Integración con service (repository mockeado) ───────────────────────────

PARENT_COLS = [
    {"id": "a", "tableId": "P", "physicalName": "A", "logicalName": "a",
     "dataType": "BIGINT", "ordinal": 0, "isPrimaryKey": True},
    {"id": "b", "tableId": "P", "physicalName": "B", "logicalName": "b",
     "dataType": "BIGINT", "ordinal": 1, "isPrimaryKey": True},
    {"id": "c", "tableId": "P", "physicalName": "C", "logicalName": "c",
     "dataType": "STRING", "ordinal": 2},
]


def _mock_repo(monkeypatch, published_cols, changes_map=None):
    """`published` devuelve lo mismo para cualquier colección — en estos flujos
    la única llamada con datos relevantes es la de canonical_columns del padre
    (la unicidad no aplica a relationships y el slice del hijo no duplica)."""
    monkeypatch.setattr(service.repository, "get",
                        AsyncMock(return_value={"projectId": "p1", "id": "c1", "status": "draft", "owner": "ana"}))
    monkeypatch.setattr(service.repository, "published", AsyncMock(return_value=published_cols))
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value=changes_map or {}))
    set_change = AsyncMock(return_value={"id": "c1", "status": "draft"})
    set_bulk = AsyncMock(return_value={"id": "c1", "status": "draft"})
    monkeypatch.setattr(service.repository, "set_change", set_change)
    monkeypatch.setattr(service.repository, "set_changes_bulk", set_bulk)
    # Doc 75 I2: el guard anti-cruce tiene sus propios tests (test_cross_project_guard).
    monkeypatch.setattr(service, "_cross_project_check", AsyncMock())
    monkeypatch.setattr(service.settings_service, "get_naming_for",
                        AsyncMock(return_value={"maxLength": 0}))
    return set_change, set_bulk


def _rel_payload(pairs):
    return {"parentTableId": "P", "childTableId": "H", "pairs": pairs,
            "parentCardinality": "one", "childCardinality": "zero-many", "identifying": True}


def test_add_change_rel_completa_pasa(monkeypatch):
    set_change, _ = _mock_repo(monkeypatch, PARENT_COLS)
    asyncio.run(service.add_change("c1", "ana", "relationships", "r1", "upsert", _rel_payload(
        [{"parentColumnId": "a", "childColumnId": "x"},
         {"parentColumnId": "b", "childColumnId": "y"}])))
    set_change.assert_called_once()


def test_add_change_rel_parcial_409(monkeypatch):
    set_change, _ = _mock_repo(monkeypatch, PARENT_COLS)
    with pytest.raises(RelationshipKeyMismatchError, match="full primary key"):
        asyncio.run(service.add_change("c1", "ana", "relationships", "r1", "upsert", _rel_payload(
            [{"parentColumnId": "a", "childColumnId": "x"}])))
    set_change.assert_not_called()


def test_add_change_respeta_pk_pendiente_del_changeset(monkeypatch):
    # El changeset ya promovió la columna 'c' a PK → la llave efectiva es a,b,c.
    pend = {"canonical_columns": {"c": {"op": "upsert", "payload": {**PARENT_COLS[2], "isPrimaryKey": True}}}}
    _mock_repo(monkeypatch, PARENT_COLS, changes_map=pend)
    with pytest.raises(RelationshipKeyMismatchError):
        asyncio.run(service.add_change("c1", "ana", "relationships", "r1", "upsert", _rel_payload(
            [{"parentColumnId": "a", "childColumnId": "x"},
             {"parentColumnId": "b", "childColumnId": "y"}])))


def test_bulk_rel_con_columnas_del_mismo_lote_pasa(monkeypatch):
    _, set_bulk = _mock_repo(monkeypatch, PARENT_COLS)
    asyncio.run(service.add_changes_bulk("c1", "ana", [
        {"collection": "canonical_columns", "entityId": "x", "op": "upsert",
         "payload": {"id": "x", "tableId": "H", "physicalName": "X", "logicalName": "x",
                     "dataType": "BIGINT", "ordinal": 0}},
        {"collection": "relationships", "entityId": "r1", "op": "upsert",
         "payload": _rel_payload([{"parentColumnId": "a", "childColumnId": "x"},
                                  {"parentColumnId": "b", "childColumnId": "y"}])},
    ]))
    set_bulk.assert_called_once()


def test_bulk_rel_parcial_no_graba_nada(monkeypatch):
    _, set_bulk = _mock_repo(monkeypatch, PARENT_COLS)
    with pytest.raises(RelationshipKeyMismatchError):
        asyncio.run(service.add_changes_bulk("c1", "ana", [
            {"collection": "relationships", "entityId": "r1", "op": "upsert",
             "payload": _rel_payload([{"parentColumnId": "a", "childColumnId": "x"}])},
        ]))
    set_bulk.assert_not_called()
