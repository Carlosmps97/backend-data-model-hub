"""Tests PUROS del traductor de updates (sin DB) — operador `$mergeObjects`.

Motivación (bug 2026-08-30): los usernames de sesión SSO son CORREOS
(`carlosmps97@hotmail.com`) y un dot-path `approvals.<actor>` los splitearía
por los puntos. `$mergeObjects` mergea un objeto dentro de un campo top-level
con las keys viajando como DATO jsonb — ningún caracter del username toca un
path ni un identificador SQL.
"""
from __future__ import annotations

import json

import pytest

from app.core.db.lakebase.translate import Sql, update_expr, upsert_doc, validate_update

ACTOR = "carlos.perez@corp.com.pe"
ENTRY = {"status": "approved", "at": "2026-08-30T21:00:00+00:00"}


def test_merge_objects_es_operador_valido():
    validate_update({"$mergeObjects": {"approvals": {ACTOR: ENTRY}}})


def test_merge_objects_key_viaja_como_dato_no_como_path():
    s = Sql()
    sql = update_expr({"$mergeObjects": {"approvals": {ACTOR: ENTRY}}}, s)
    assert "jsonb_set" in sql and "||" in sql
    # El campo raíz va como path de UN solo segmento…
    assert ["approvals"] in [p for p in s.params if isinstance(p, list)]
    # …y el correo COMPLETO viaja dentro del parámetro JSON (nunca spliteado).
    jsons = [p for p in s.params if isinstance(p, str) and p.startswith("{")]
    assert any(ACTOR in json.loads(p) for p in jsons)
    # Jamás un text[] con el correo partido por puntos.
    assert all("carlos" not in seg for p in s.params if isinstance(p, list) for seg in p)


def test_merge_objects_convive_con_set_simple():
    s = Sql()
    sql = update_expr(
        {"$mergeObjects": {"approvals": {ACTOR: ENTRY}}, "$set": {"updatedAt": "T1"}}, s)
    assert "jsonb_set" in sql  # merge + set simple en la misma expresión


def test_merge_objects_rechaza_campo_anidado():
    with pytest.raises(NotImplementedError):
        update_expr({"$mergeObjects": {"a.b": {"k": 1}}}, Sql())


def test_merge_objects_rechaza_valor_no_objeto():
    with pytest.raises(NotImplementedError):
        update_expr({"$mergeObjects": {"approvals": "x"}}, Sql())


def test_merge_objects_en_upsert_doc():
    base = upsert_doc(
        {"_id": "x"},
        {"$mergeObjects": {"approvals": {ACTOR: ENTRY}}, "$set": {"updatedAt": "T1"}})
    assert base["approvals"] == {ACTOR: ENTRY}
    assert base["updatedAt"] == "T1"
