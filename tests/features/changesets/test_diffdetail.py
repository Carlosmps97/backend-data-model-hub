"""Diff de campos ANTES→DESPUÉS por entidad (doc 31, `diffdetail.py`) — puro.

El popup "Change details" de la revisión muestra por entidad SOLO los campos
que cambian, con referencias resueltas a NOMBRE (UDP, dominios, tablas/columnas
de relaciones y vistas) y el ruido interno excluido (timestamps, flgactive,
`layout` del canvas). `before` = imagen histórica si el cambio la trae
estampada del publish; si no, el documento publicado vivo que pasa el service.
"""
from __future__ import annotations

from app.features.changesets.diffdetail import collect_ref_ids, entity_detail

RES = {
    "udp": {"u-dac": "Clasificacion del Dato", "u-vac": "Frecuencia Vacuum"},
    "domains": {"d-monto": "Monto"},
    "tables": {"t1": "HD_VENTA", "t2": "HD_RIESGO"},
    "columns": {"c1": "CODCLAVECTA", "c2": "CODCLAVE_R"},
    "projects": {"p1": "DDV"},
    "folders": {"f1": "Despriorizado"},
}


def _fields_by_key(detail: dict) -> dict[str, dict]:
    return {f["key"]: f for f in detail["fields"]}


# ── created / modified / deleted básicos ───────────────────────────────────


def test_columna_creada_lista_campos_con_nombres_resueltos():
    change = {"op": "upsert", "payload": {
        "tableId": "t1", "physicalName": "NUEVACOL", "logicalName": "nueva",
        "dataType": "STRING", "isNullable": True, "isPrimaryKey": False,
        "ordinal": 0, "parentDomainId": "d-monto",
        "udpValues": {"u-dac": "No DAC"}, "updatedAt": "2026-01-01",
    }}
    d = entity_detail("canonical_columns", "c9", change, None, RES)
    assert d["action"] == "created"
    assert d["name"] == "NUEVACOL"
    by = _fields_by_key(d)
    assert by["physicalName"]["after"] == "NUEVACOL" and by["physicalName"]["before"] is None
    assert by["parentDomainId"]["after"] == "Monto"          # id → nombre
    assert by["udp:u-dac"]["label"] == "UDP · Clasificacion del Dato"
    assert by["udp:u-dac"]["after"] == "No DAC"
    # ordinal 0 es valor REAL (no skippable); ruido y flags False afuera
    assert "ordinal" in by
    assert "updatedAt" not in by and "tableId" not in by and "isPrimaryKey" not in by


def test_tabla_modificada_solo_campos_cambiados():
    before = {"physicalName": "HD_VTA", "logicalName": "venta", "schema": "core",
              "description": None, "udpValues": {"u-vac": "DAILY_15 days"},
              "updatedAt": "2026-01-01", "flgactive": True}
    change = {"op": "upsert", "payload": {
        "physicalName": "HD_VTA2", "logicalName": "venta", "schema": "core",
        "description": "", "udpValues": {"u-vac": "CUSTOM_90 days"},
        "updatedAt": "2026-02-02"}}
    d = entity_detail("canonical_tables", "t9", change, before, RES)
    assert d["action"] == "modified"
    by = _fields_by_key(d)
    assert set(by) == {"physicalName", "udp:u-vac"}       # rename + UDP; '' ≡ None
    assert by["physicalName"]["before"] == "HD_VTA" and by["physicalName"]["after"] == "HD_VTA2"
    assert by["udp:u-vac"]["before"] == "DAILY_15 days"
    assert by["udp:u-vac"]["after"] == "CUSTOM_90 days"


def test_delete_usa_el_publicado_como_before():
    before = {"physicalName": "TMP_X", "logicalName": "tmp", "schema": "core",
              "udpValues": {}, "flgactive": True}
    d = entity_detail("canonical_tables", "t9", {"op": "delete"}, before, RES)
    assert d["action"] == "deleted" and d["name"] == "TMP_X"
    by = _fields_by_key(d)
    assert by["physicalName"]["before"] == "TMP_X" and by["physicalName"]["after"] is None


def test_before_estampado_manda_sobre_el_publicado_vivo():
    """Changeset APLICADO: el publicado vivo ya avanzó — el histórico manda."""
    change = {"op": "upsert", "beforeAt": "2026-01-01",
              "before": {"physicalName": "VIEJA", "logicalName": "x"},
              "payload": {"physicalName": "NUEVA", "logicalName": "x"}}
    publicado_vivo = {"physicalName": "NUEVA", "logicalName": "x"}   # ya aplicado
    d = entity_detail("canonical_tables", "t1", change, publicado_vivo, RES)
    assert d["action"] == "modified"
    assert _fields_by_key(d)["physicalName"]["before"] == "VIEJA"


def test_before_estampado_none_es_created_historico():
    change = {"op": "upsert", "beforeAt": "2026-01-01", "before": None,
              "payload": {"physicalName": "NUEVA", "logicalName": "x"}}
    d = entity_detail("canonical_tables", "t1", change, {"physicalName": "NUEVA"}, RES)
    assert d["action"] == "created"


# ── relaciones ─────────────────────────────────────────────────────────────


def test_relacion_nombre_pares_y_cardinalidad_resueltos():
    before = {"parentTableId": "t1", "childTableId": "t2",
              "parentCardinality": "one", "childCardinality": "zero-many",
              "identifying": False,
              "pairs": [{"parentColumnId": "c1", "childColumnId": "c2"}]}
    change = {"op": "upsert", "payload": {
        **before, "childCardinality": "one-many",
        "pairs": [{"parentColumnId": "c1", "childColumnId": "c2", "roleName": "FK_R"}]}}
    d = entity_detail("relationships", "r1", change, before, RES)
    assert d["name"] == "HD_VENTA → HD_RIESGO"                # ids → nombres
    by = _fields_by_key(d)
    assert by["childCardinality"]["before"] == "zero-many"
    assert by["pairs"]["after"] == "CODCLAVECTA → CODCLAVE_R (as FK_R)"
    assert "parentTableId" not in by                          # no cambió


def test_relacion_eliminada_muestra_extremos_por_nombre():
    before = {"parentTableId": "t1", "childTableId": "t2", "parentCardinality": "one",
              "childCardinality": "zero-many", "identifying": True,
              "pairs": [{"parentColumnId": "c1", "childColumnId": "c2"}]}
    d = entity_detail("relationships", "r1", {"op": "delete"}, before, RES)
    assert d["action"] == "deleted" and d["name"] == "HD_VENTA → HD_RIESGO"
    by = _fields_by_key(d)
    assert by["parentTableId"]["before"] == "HD_VENTA"
    assert by["identifying"]["before"] is True
    assert by["pairs"]["before"] == "CODCLAVECTA → CODCLAVE_R"


# ── vistas ─────────────────────────────────────────────────────────────────


def test_vista_modificada_delta_de_columnas_y_definiciones():
    before = {"name": "vw_venta", "schema": "core_v", "showOnCanvas": True,
              "sourceTableIds": ["t1"], "sql": "SELECT a",
              "sources": [{"tableId": "t1", "column": "a"},
                          {"tableId": "t1", "column": "b", "description": "vieja"}]}
    change = {"op": "upsert", "payload": {
        **before, "sql": "SELECT a2",
        "sources": [{"tableId": "t1", "column": "a"},
                    {"tableId": "t1", "column": "b", "description": "nueva"},
                    {"tableId": "t1", "column": "c"}]}}
    d = entity_detail("views", "v1", change, before, RES)
    by = _fields_by_key(d)
    assert "sql" not in by and "sourceTableIds" not in by      # ruido de views
    assert by["src:t1"]["kind"] == "delta" and by["src:t1"]["after"] == "+c"
    assert by["src:t1"]["label"] == "Columns · HD_VENTA"
    assert by["srcdef:t1"]["after"] == "~b"                    # definición cambiada


def test_vista_creada_resume_columnas_por_fuente():
    change = {"op": "upsert", "payload": {
        "name": "vw_nueva", "schema": "core_v", "showOnCanvas": True,
        "sources": [{"tableId": "t1", "column": "a"}, {"tableId": "t1", "column": "b"}]}}
    d = entity_detail("views", "v1", change, None, RES)
    by = _fields_by_key(d)
    assert by["name"]["after"] == "vw_nueva"
    assert by["showOnCanvas"]["after"] is True
    assert by["src:t1"]["after"] == "2 columns: a, b"


# ── estructura (canvas) ────────────────────────────────────────────────────


def test_canvas_solo_layout_no_tiene_campos_visibles():
    before = {"name": "Pricing", "projectId": "p1", "folderId": "f1",
              "tableIds": ["t1"], "layout": {"t1": {"x": 1, "y": 2}}, "udpValues": {}}
    change = {"op": "upsert", "payload": {
        **before, "layout": {"t1": {"x": 99, "y": 99}}}}
    d = entity_detail("subject_areas", "sa1", change, before, RES)
    assert d["action"] == "modified" and d["fields"] == []     # layout = ruido


def test_canvas_membresia_de_tablas_como_delta():
    before = {"name": "Pricing", "projectId": "p1", "tableIds": ["t1"], "layout": {}}
    change = {"op": "upsert", "payload": {**before, "tableIds": ["t1", "t2", "t3"]}}
    d = entity_detail("subject_areas", "sa1", change, before, RES)
    by = _fields_by_key(d)
    assert by["tableIds"]["kind"] == "delta" and by["tableIds"]["after"] == "+2 added"
    # projectId sin cambio no aparece; el nombre resuelto solo en created/deleted
    assert "projectId" not in by


def test_campo_desconocido_sale_con_label_prettificado():
    before = {"physicalName": "X", "logicalName": "x", "campoRaro": "a"}
    change = {"op": "upsert", "payload": {"physicalName": "X", "logicalName": "x",
                                          "campoRaro": "b"}}
    d = entity_detail("canonical_tables", "t1", change, before, RES)
    by = _fields_by_key(d)
    assert by["campoRaro"]["label"] == "Campo raro"
    assert by["campoRaro"]["before"] == "a" and by["campoRaro"]["after"] == "b"


# ── collect_ref_ids ────────────────────────────────────────────────────────


def test_collect_ref_ids_junta_tablas_y_columnas_de_rel_y_vistas():
    entries = [
        ("relationships",
         {"parentTableId": "t1", "childTableId": "t2",
          "pairs": [{"parentColumnId": "c1", "childColumnId": "c2"}]},
         {"parentTableId": "t1", "childTableId": "t3",
          "pairs": [{"parentColumnId": "c1", "childColumnId": "c9"}]}),
        ("views", None, {"sourceTableIds": ["t4"],
                         "sources": [{"tableId": "t5", "column": "a"}]}),
        ("canonical_tables", {"physicalName": "X"}, {"physicalName": "Y"}),
    ]
    tids, cids = collect_ref_ids(entries)
    assert tids == {"t1", "t2", "t3", "t4", "t5"}
    assert cids == {"c1", "c2", "c9"}


def test_pk_position_retirado_no_es_revisable():
    """Final review #5: `pkPosition` se retiró (doc 94 D1). Un doc viejo que aún
    lo trae no debe mostrar «Pk position: 0 → —» al editar la columna."""
    before = {"physicalName": "COD", "logicalName": "cod", "isPrimaryKey": True, "ordinal": 0, "pkPosition": 0}
    change = {"op": "upsert", "payload": {"physicalName": "COD", "logicalName": "cod", "isPrimaryKey": True,
                                          "ordinal": 1}}
    d = entity_detail("canonical_columns", "c9", change, before, RES)
    assert set(_fields_by_key(d)) == {"ordinal"}
