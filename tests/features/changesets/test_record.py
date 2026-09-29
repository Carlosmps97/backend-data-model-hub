"""Validación de payloads de cambios (`changesets/validation.py`) — pura, sin DB.

Un upsert de changeset termina APLICADO tal cual a la colección publicada en el
publish: payloads que no validan contra el modelo de su colección se rechazan
en `add_change` (422) y como gate autoritativo en el apply.
"""
from __future__ import annotations

import pytest

from app.features.changesets.validation import (
    client_keys_error, client_routes_error, payload_error, validate_changes)


def test_upsert_valido_pasa():
    payload = {"projectId": "p1", "tableId": "t1", "physicalName": "ID_CTA", "logicalName": "id cuenta",
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
    payload = {"projectId": "p1", "physicalName": "CTA", "logicalName": "cuenta", "schema": "core"}
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


# ── Doc 99: trazos manuales de wires (`routes` del canvas) ──────────────────

_CANVAS = {"projectId": "p1", "name": "Canvas", "tableIds": ["t1"], "layout": {"t1": {"x": 0, "y": 0}}}
_BAD_ROUTES = {"rel-1": [{"x": "a", "y": 2}]}


def test_cliente_canvas_con_routes_valido_pasa():
    payload = {**_CANVAS, "routes": {"rel-1": [{"x": 420, "y": 180}], "subsym-s1": [{"x": 1.5, "y": 2}]}}
    assert payload_error("subject_areas", "sa1", "upsert", payload) is None
    assert client_routes_error("subject_areas", "sa1", "upsert", payload) is None


def test_cliente_canvas_sin_la_llave_routes_pasa():
    assert client_routes_error("subject_areas", "sa1", "upsert", dict(_CANVAS)) is None
    assert client_routes_error("subject_areas", "sa1", "upsert", {**_CANVAS, "routes": None}) is None


def test_cliente_canvas_con_routes_malformado_falla_legible():
    err = client_routes_error("subject_areas", "sa1", "upsert", {**_CANVAS, "routes": _BAD_ROUTES})
    assert err is not None
    assert err.startswith("subject_areas/sa1: routes.rel-1[0]")
    assert "finite number" in err


def test_el_chequeo_estricto_es_solo_de_canvases_y_de_upserts():
    assert client_routes_error("folders", "f1", "upsert", {"projectId": "p1", "name": "F", "routes": "x"}) is None
    assert client_routes_error("subject_areas", "sa1", "delete", None) is None


def test_el_gate_del_publish_no_falla_por_un_trazo_corrupto():
    """Un trazo viejo corrupto (edición a mano de la BD) viaja en los payloads
    que arma el backend — cascada de membresía, rollback — y NO puede bloquear
    la publicación de quien sólo borró una tabla: se sanea al aplicar."""
    changes = {"subject_areas": {
        "sa-viejo": {"op": "upsert", "payload": {**_CANVAS, "routes": _BAD_ROUTES}},
        "sa-raro": {"op": "upsert", "payload": {**_CANVAS, "routes": ["no", "es", "mapa"]}},
    }}
    assert payload_error("subject_areas", "sa-viejo", "upsert", changes["subject_areas"]["sa-viejo"]["payload"]) is None
    assert validate_changes(changes) == []


def test_una_llave_con_punto_hacia_routes_no_esquiva_el_chequeo():
    """El publish mete TODAS las llaves del payload en el `$set`: una llave de
    primer nivel `routes.x` escribiría dentro de `routes` sin pasar por el
    chequeo de `payload["routes"]`."""
    payload = {**_CANVAS, "routes": {"rel-1": [{"x": 1, "y": 2}]}, "routes.evil": [{"x": "a"}]}
    err = client_routes_error("subject_areas", "sa1", "upsert", payload)
    assert err is not None and err.startswith("subject_areas/sa1: routes") and "routes.evil" in err
    assert client_routes_error("folders", "f1", "upsert", {"projectId": "p1", "name": "F", "routes.x": 1}) is None


# ── Doc 100 (P1/P2): llaves de primer nivel reservadas ─────────────────────
# El publish mete TODAS las llaves del payload en el `$set`, y la validación
# contra el modelo ignora las que no conoce (`extra="ignore"`): `layout.evil`
# escribía DENTRO del layout y rompía la lectura del canvas, `$x` hacía fallar
# el approve y `_id` cambiaba la identidad del registro.


@pytest.mark.parametrize("key", ["layout.evil", "$foo", "_id"])
def test_llave_reservada_da_error_legible(key):
    err = client_keys_error("subject_areas", "sa1", "upsert", {**_CANVAS, key: {"x": "a"}})
    assert err is not None
    assert err.startswith("subject_areas/sa1: ") and f"'{key}'" in err


def test_la_regla_vale_para_toda_coleccion():
    payload = {"projectId": "p1", "physicalName": "T", "logicalName": "t", "udpValues.x": "v"}
    assert payload_error("canonical_tables", "t1", "upsert", payload) is None   # el modelo la ignora
    assert "'udpValues.x'" in client_keys_error("canonical_tables", "t1", "upsert", payload)


def test_llaves_normales_deletes_y_payload_vacio_pasan():
    assert client_keys_error("subject_areas", "sa1", "upsert", dict(_CANVAS)) is None
    assert client_keys_error("subject_areas", "sa1", "upsert", {**_CANVAS, "routes": {}, "a$b": 1}) is None
    assert client_keys_error("subject_areas", "sa1", "delete", None) is None
    assert client_keys_error("subject_areas", "sa1", "upsert", None) is None
