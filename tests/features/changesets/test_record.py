"""Validación de payloads de cambios (`changesets/validation.py`) — pura, sin DB.

Un upsert de changeset termina APLICADO tal cual a la colección publicada en el
publish: payloads que no validan contra el modelo de su colección se rechazan
en `add_change` (422) y como gate autoritativo en el apply.
"""
from __future__ import annotations

from app.features.changesets.validation import payload_error, validate_changes


def test_upsert_valido_pasa():
    payload = {"tableId": "t1", "physicalName": "ID_CTA", "logicalName": "id cuenta",
               "dataType": "BIGINT", "ordinal": 0}
    assert payload_error("canonical_columns", "c1", "upsert", payload) is None


def test_upsert_sin_campos_requeridos_falla_legible():
    err = payload_error("canonical_columns", "c1", "upsert", {"logicalName": "x"})
    assert err is not None
    assert "canonical_columns/c1" in err
    assert "tableId" in err  # nombra el campo faltante


def test_delete_no_valida_payload():
    assert payload_error("canonical_columns", "c1", "delete", None) is None


def test_relationship_requiere_extremos():
    # Payload legacy incompleto: el validator lo normaliza a v2 y falla por los
    # campos que siguen faltando (parentTableId/pares) — nombra el campo v2.
    err = payload_error("relationships", "r1", "upsert", {"sourceTableId": "a"})
    assert err is not None and "parentTableId" in err


def test_tabla_acepta_alias_schema():
    payload = {"physicalName": "CTA", "logicalName": "cuenta", "schema": "core"}
    assert payload_error("canonical_tables", "t1", "upsert", payload) is None


def test_coleccion_desconocida_es_defensivo_none():
    # La whitelist del router ya corta colecciones no versionadas; el validador
    # no debe explotar si igual le llega una.
    assert payload_error("otra_coleccion", "x", "upsert", {"a": 1}) is None


def test_validate_changes_junta_errores_de_todas_las_colecciones():
    changes = {
        "canonical_tables": {"t1": {"op": "upsert", "payload": {"physicalName": "SOLO"}}},
        "canonical_columns": {"c1": {"op": "delete"}},
        "relationships": {"r1": {"op": "upsert", "payload": {}}},
    }
    errors = validate_changes(changes)
    assert len(errors) == 2  # t1 (falta logicalName) + r1 (faltan extremos); c1 delete pasa
    assert any("canonical_tables/t1" in e for e in errors)
    assert any("relationships/r1" in e for e in errors)


def test_validate_changes_vacio_ok():
    assert validate_changes({}) == []
    assert validate_changes(None) == []


def test_safe_path_part_guard_de_dot_path():
    """`set_approval` sigue armando dot-paths de Mongo (`approvals.<actor>`):
    segmentos con '.' o que empiecen con '$' se rechazan (inyección de path)."""
    from app.features.changesets.repository import _safe_path_part

    assert _safe_path_part("canonical_columns")
    assert _safe_path_part("col-123e4567-e89b")
    assert not _safe_path_part("")
    assert not _safe_path_part("a.b")
    assert not _safe_path_part("$set")
