"""Unicidad de nombres (spec 10 §9 + doc 50) — funciones puras de `validation.py`.

Tablas: physicalName case-insensitive GLOBAL (doc 50 — el esquema ya no
participa en la clave). Columnas: physicalName case-insensitive dentro de su
tableId. El estado comparado es el EFECTIVO: publicado + upserts pendientes
del mismo changeset, menos sus deletes. El grandfather de homónimos legacy es
del SERVICE (necesita el publicado por _id) — ver test_add_change_duplicates.
"""
from __future__ import annotations

from app.features.changesets.validation import duplicate_error

PUB_T = [{"id": "t1", "physicalName": "CLIENTE", "logicalName": "cliente", "schema": "core"}]
PUB_C = [{"id": "c1", "tableId": "t1", "physicalName": "ID_CTA", "logicalName": "id", "dataType": "BIGINT"}]


def test_tabla_duplicada_contra_publicado_case_insensitive():
    # El mensaje nombra el esquema del HOMÓNIMO existente (clave global, doc 50).
    err = duplicate_error("canonical_tables", "t9",
                          {"physicalName": "cliente", "logicalName": "x", "schema": "CORE"},
                          PUB_T, {})
    assert err == "Table cliente already exists (schema core)"


def test_tabla_misma_entidad_no_conflicta():
    # Re-save / rename de la MISMA tabla: su doc publicado se excluye por id.
    assert duplicate_error("canonical_tables", "t1",
                           {"physicalName": "CLIENTE", "logicalName": "cliente v2", "schema": "core"},
                           PUB_T, {}) is None


def test_tabla_otro_schema_tambien_conflicta():
    # Doc 50: la clave es GLOBAL — el mismo físico en OTRO esquema bloquea.
    err = duplicate_error("canonical_tables", "t9",
                          {"physicalName": "CLIENTE", "logicalName": "x", "schema": "stage"},
                          PUB_T, {})
    assert err == "Table CLIENTE already exists (schema core)"


def test_tabla_duplicada_contra_pendiente_del_changeset():
    pending = {"t8": {"op": "upsert", "payload": {"physicalName": "NUEVA", "logicalName": "n", "schema": "core"}}}
    err = duplicate_error("canonical_tables", "t9",
                          {"physicalName": "nueva", "logicalName": "x", "schema": "CORE"},
                          [], pending)
    assert err is not None


def test_delete_pendiente_libera_el_nombre():
    # El changeset borra la tabla publicada homónima: el nombre queda libre.
    pending = {"t1": {"op": "delete"}}
    assert duplicate_error("canonical_tables", "t9",
                           {"physicalName": "CLIENTE", "logicalName": "x", "schema": "core"},
                           PUB_T, pending) is None


def test_tabla_sin_schema_matchea_sin_schema():
    pub = [{"id": "t1", "physicalName": "CLIENTE", "logicalName": "cliente"}]
    err = duplicate_error("canonical_tables", "t9",
                          {"physicalName": "cliente", "logicalName": "x"}, pub, {})
    assert err == "Table cliente already exists"


def test_columna_duplicada_en_misma_tabla():
    err = duplicate_error("canonical_columns", "c9",
                          {"tableId": "t1", "physicalName": "id_cta", "logicalName": "x", "dataType": "STRING"},
                          PUB_C, {})
    assert err == "Column id_cta already exists in this table"


def test_columna_misma_entidad_no_conflicta():
    assert duplicate_error("canonical_columns", "c1",
                           {"tableId": "t1", "physicalName": "ID_CTA", "logicalName": "id v2", "dataType": "BIGINT"},
                           PUB_C, {}) is None


def test_columna_mismo_nombre_en_otra_tabla_pasa():
    assert duplicate_error("canonical_columns", "c9",
                           {"tableId": "t2", "physicalName": "ID_CTA", "logicalName": "x", "dataType": "STRING"},
                           [], {}) is None


def test_otras_colecciones_no_chequean():
    assert duplicate_error("relationships", "r1", {"sourceTableId": "a"}, [], {}) is None
