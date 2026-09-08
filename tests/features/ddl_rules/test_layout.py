"""Doc 73/76 · Column layout: `action.layout = {partitionColumns: 'last',
partitionOrderUdp: 'Particion'}` emite las columnas de partición al FINAL del
CREATE (físico en el front, tablas generadas en el motor), ordenadas por el
correlativo PART_nn del UDP, sin tocar el orden físico del modelo. Validación,
opciones efectivas del pipeline y /test."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.ddl_rules.engine import generators as g
from app.features.ddl_rules.engine import pipeline
from app.features.ddl_rules.engine import validate as v
from app.features.ddl_rules.engine.context import column_ctx, names_by_id, table_ctx
from app.features.ddl_rules.templates import SEED_RULES, TEMPLATES

LAYOUT = {"id": "l1", "name": "particiones_al_final", "kind": "rule", "target": "table",
          "condition": "", "action": {"layout": {"partitionColumns": "last"}},
          "appliesTo": ["ddl.tabla_fisica"], "priority": 30, "enabled": True, "validationState": "valid"}
LAYOUT_UDP = {**LAYOUT, "action": {"layout": {"partitionColumns": "last", "partitionOrderUdp": "Particion"}}}
REJ = {"id": "g1", "name": "tabla_rechazos", "kind": "generator", "sourceArtifact": "ddl.tabla_fisica",
       "condition": "true", "priority": 200, "enabled": True, "validationState": "valid",
       "action": {"emit": {"artifact": "ddl.tabla_rej", "type": "table", "schema": "{tabla.esquema}",
                           "name": "{tabla.nombre}_rej", "columns": {"inherit": "all", "force_type": "STRING",
                                                                    "strip": ["not_null", "pk"]}}}}
TABLE = {"id": "t1", "physicalName": "tbl_x", "schema": "core", "udpValues": {}}
DEFS = [{"id": "u-part", "name": "Particion", "level": "column", "dataType": "list",
         "allowedValues": ["No Definido", "PART_01", "PART_02", "PART_03"]}]
# orden físico: codmes (partición PART_02) va PRIMERA; fecdia (partición PART_01) en el medio.
COLS = [
    {"physicalName": "codmes", "dataType": "INT", "ordinal": 0, "isPartition": True, "udpValues": {"u-part": "PART_02"}},
    {"physicalName": "cod_cli", "dataType": "STRING", "ordinal": 1, "isPrimaryKey": True, "isNullable": False, "udpValues": {}},
    {"physicalName": "fecdia", "dataType": "DATE", "ordinal": 2, "isPartition": True, "udpValues": {"u-part": "PART_01"}},
    {"physicalName": "monto", "dataType": "DECIMAL(18,2)", "ordinal": 3, "udpValues": {}},
]
N = names_by_id(DEFS)
BASE = {"tabla": table_ctx(TABLE, N), "modelo": {"nombre": "DDV", "udp": {}}}
CTX = {c["physicalName"]: column_ctx(c, N) for c in COLS}
CONFIG = {"lookups": {}, "functions": []}


def test_partition_correlative():
    assert g.partition_correlative("PART_01") == 1 and g.partition_correlative("part-12") == 12
    assert g.partition_correlative("No Definido") is None and g.partition_correlative(None) is None


def test_layout_from_rules_solo_reglas_de_tabla_sobre_la_fisica():
    assert g.layout_from_rules([LAYOUT]) == {"partitionsLast": True, "partitionOrderUdp": None}
    assert g.layout_from_rules([LAYOUT_UDP]) == {"partitionsLast": True, "partitionOrderUdp": "Particion"}
    off = {"partitionsLast": False, "partitionOrderUdp": None}
    assert g.layout_from_rules([{**LAYOUT, "appliesTo": ["ddl.tabla_rej"]}]) == off
    assert g.layout_from_rules([{**LAYOUT, "action": {"layout": {"partitionColumns": "keep"}}}]) == off
    assert g.layout_from_rules([REJ]) == off


def test_layout_columns_estable_y_sin_opcion_intacto():
    cols = [{"name": "a", "partition": True}, {"name": "b"}, {"name": "c", "partition": True}, {"name": "d"}]
    assert [c["name"] for c in g.layout_columns(cols, {"partitionsLast": True})] == ["b", "d", "a", "c"]
    assert [c["name"] for c in g.layout_columns(cols, None)] == ["a", "b", "c", "d"]
    # con correlativos: PART_01 antes que PART_02 aunque el orden físico diga lo contrario;
    # las particiones SIN correlativo van después, en orden físico.
    cols2 = [{"name": "a", "partition": True, "partitionOrder": 2}, {"name": "b"},
             {"name": "c", "partition": True, "partitionOrder": None},
             {"name": "d", "partition": True, "partitionOrder": 1}]
    assert [c["name"] for c in g.layout_columns(cols2, {"partitionsLast": True})] == ["b", "d", "a", "c"]


def test_generador_orden_fisico_explicito_y_particiones_al_final_siempre():
    # columnas de ENTRADA desordenadas a propósito: el motor ordena por `ordinal`
    # (doc 73 O2) y las tablas generadas emiten las particiones al final SIEMPRE
    # (pedido owner 07-20) — con o sin la regla de layout.
    shuffled = [COLS[2], COLS[0], COLS[3], COLS[1]]
    stmts, _ = g.run_generators([REJ], TABLE, shuffled, BASE, CTX, CONFIG, {})
    sql = stmts[0]["sql"]
    body = sql.split("(\n", 1)[1].split("\n)", 1)[0]
    assert [line.split()[0].strip("`") for line in body.split(",\n")] == ["cod_cli", "monto", "codmes", "fecdia"]
    assert "PARTITIONED BY (`codmes`, `fecdia`)" in sql        # sin regla de orden: orden físico


def test_generador_ordena_particiones_por_udp_cuando_la_regla_lo_declara():
    """Doc 76 D3: con `partitionOrderUdp`, PART_01 (fecdia) va antes que PART_02
    (codmes) en el bloque final del CREATE y en PARTITIONED BY."""
    stmts, _ = g.run_generators([REJ, LAYOUT_UDP], TABLE, COLS, BASE, CTX, CONFIG, {})
    sql = stmts[0]["sql"]
    body = sql.split("(\n", 1)[1].split("\n)", 1)[0]
    assert [line.split()[0].strip("`") for line in body.split(",\n")] == ["cod_cli", "monto", "fecdia", "codmes"]
    assert "PARTITIONED BY (`fecdia`, `codmes`)" in sql


def test_pipeline_deriva_el_layout_de_las_reglas_que_corren():
    payload = {"model": {"name": "DDV", "udpValues": {}}, "options": {},
               "tables": [{"table": TABLE, "columns": COLS, "baseSql": "CREATE TABLE core.tbl_x (x INT);"}],
               "views": []}
    out = pipeline.render_export(payload, [LAYOUT, REJ], CONFIG, [], {})
    # la regla de layout no emite sentencias propias: queda en el log como aplicada
    assert [s["artifact"] for s in out["statements"]] == ["ddl.tabla_fisica", "ddl.tabla_rej"]
    applied = [e for e in out["log"] if e.get("rule") == "particiones_al_final"]
    assert applied and applied[0]["status"] == "applied" and "partition columns last" in applied[0]["object"]
    # con UDP de orden el log lo dice
    out_udp = pipeline.render_export(payload, [LAYOUT_UDP, REJ], CONFIG, DEFS, {})
    applied_udp = [e for e in out_udp["log"] if e.get("rule") == "particiones_al_final"]
    assert "ordered by UDP 'Particion'" in applied_udp[0]["object"]
    # deshabilitada → skipped, no aplicada
    out2 = pipeline.render_export(payload, [{**LAYOUT, "enabled": False}, REJ], CONFIG, [], {})
    assert [e["status"] for e in out2["log"] if e.get("rule") == "particiones_al_final"] == ["skipped"]


ARTS = ["ddl.tabla_fisica", "ddl.vista_negocio", "ddl.tabla_rej"]


def test_validate_layout_ok_y_errores_estructurales():
    ok = v.validate_rule(LAYOUT, [], {}, ARTS, {"ddl.tabla_fisica": "table"})
    assert ok["state"] == "valid" and ok["errors"] == []
    bad = v.validate_rule({**LAYOUT, "condition": "tabla.tipo = 'physical'", "target": "column",
                           "action": {"layout": {"partitionColumns": "first", "zzz": 1}, "tags": {"a": "b"}},
                           "appliesTo": ["ddl.tabla_fisica", "ddl.tabla_rej"]}, [], {}, ARTS, {})
    msgs = " | ".join(e["message"] for e in bad["errors"])
    assert bad["state"] == "invalid"
    assert "must be one of: keep, last" in msgs and "Unknown column layout setting 'zzz'" in msgs
    assert "table rule" in msgs and "leave the condition empty" in msgs and "can't be combined" in msgs
    assert any("only applies to the physical table" in w for w in bad["warnings"])
    empty = v.validate_rule({**LAYOUT, "action": {"layout": {}}}, [], {}, ARTS, {})
    assert empty["state"] == "invalid"


def test_validate_partition_order_udp_enlaza_el_udp_o_falla():
    """Doc 76 D3: `partitionOrderUdp` debe ser un UDP de COLUMNA existente; el
    binding entra a `udpRefs` (borrar/renombrar el UDP queda protegido)."""
    ok = v.validate_rule(LAYOUT_UDP, DEFS, {}, ARTS, {"ddl.tabla_fisica": "table"})
    assert ok["state"] == "valid" and ok["udpRefs"] == [{"udpId": "u-part", "level": "column"}]
    bad = v.validate_rule({**LAYOUT_UDP, "action": {"layout": {"partitionColumns": "last",
                                                               "partitionOrderUdp": "Particio"}}},
                          DEFS, {}, ARTS, {})
    assert bad["state"] == "invalid"
    err = next(e for e in bad["errors"] if e["check"] == "UDP exists in catalog")
    assert "Particio" in err["message"] and err["suggestion"] == "Particion"
    vacio = v.validate_rule({**LAYOUT_UDP, "action": {"layout": {"partitionOrderUdp": ""}}}, DEFS, {}, ARTS, {})
    assert vacio["state"] == "invalid"


def test_semilla_y_plantilla_de_layout():
    seed = next(r for r in SEED_RULES if r["name"] == "particiones_al_final")
    assert seed["action"] == {"layout": {"partitionColumns": "last", "partitionOrderUdp": "Particion"}}
    assert seed["condition"] == ""
    assert v.validate_rule({**seed, "id": None, "udpRefs": []}, DEFS, {}, ARTS, {})["state"] == "valid"
    assert any(t["id"] == "partitions-last" for t in TEMPLATES)


def test_service_test_rule_layout(monkeypatch):
    from app.features.ddl_rules import service as svc
    monkeypatch.setattr(svc.udp_repo, "list_udp", AsyncMock(return_value=DEFS))
    monkeypatch.setattr(svc.repository, "get_config", AsyncMock(return_value={"id": "global", "lookups": {}, "functions": []}))
    monkeypatch.setattr(svc.repository, "list_rules", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc.dom_repo, "list_domains", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc.catalog_repo, "list_tables_by_ids", AsyncMock(return_value=[TABLE]))
    monkeypatch.setattr(svc.catalog_repo, "list_columns", AsyncMock(return_value=COLS))
    out = asyncio.run(svc.test_rule("p1", LAYOUT, "t1"))
    assert out["matched"] == 1
    frag = out["fragments"][0]
    assert frag["sql"] == "-- column order: cod_cli, monto, codmes, fecdia"
    assert frag["why"] == "partition columns last: codmes, fecdia"
    out_udp = asyncio.run(svc.test_rule("p1", LAYOUT_UDP, "t1"))
    frag_udp = out_udp["fragments"][0]
    assert frag_udp["sql"] == "-- column order: cod_cli, monto, fecdia, codmes"
    assert frag_udp["why"] == "partition columns last: fecdia, codmes · ordered by UDP 'Particion'"
