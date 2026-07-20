"""Lógica PURA del script de migración F3a (`build_ops`): idempotencia (salta
docs ya migrados), materialización tableId→sourceTableIds y conteos. pymongo
UpdateOne implementa __eq__, así que se compara por igualdad directa."""
from __future__ import annotations

from pymongo import UpdateOne

from scripts.migrate_view_source_tables import build_ops


def test_build_ops_migra_solo_los_legacy():
    views = [
        {"_id": "v1", "tableId": "t1"},                            # a migrar
        {"_id": "v2", "tableId": "t2", "sourceTableIds": ["t2"]},  # ya migrada
        {"_id": "v3"},                                             # sin tableId
        {"_id": "v4", "tableId": "t4", "sourceTableIds": []},      # vacio = legacy
    ]
    ops, migrate, skipped, no_table = build_ops(views)
    assert (migrate, skipped, no_table) == (2, 1, 1)
    assert ops == [
        UpdateOne({"_id": "v1"}, {"$set": {"sourceTableIds": ["t1"]}}),
        UpdateOne({"_id": "v4"}, {"$set": {"sourceTableIds": ["t4"]}}),
    ]


def test_build_ops_idempotente_segunda_pasada_no_op():
    # Tras aplicar la migración, una segunda corrida no genera escrituras.
    migrated = [{"_id": "v1", "tableId": "t1", "sourceTableIds": ["t1"]}]
    ops, migrate, skipped, no_table = build_ops(migrated)
    assert ops == []
    assert (migrate, skipped, no_table) == (0, 1, 0)


def test_build_ops_conserva_tableid():
    # `tableId` NO se toca (compat legacy / rollback): el $set solo escribe
    # sourceTableIds.
    ops, *_ = build_ops([{"_id": "v1", "tableId": "t1"}])
    assert ops[0]._doc == {"$set": {"sourceTableIds": ["t1"]}}
