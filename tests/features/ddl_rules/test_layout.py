"""Doc 73/76/93 · Column layout: `action.layout = {partitionColumns: 'last',
partitionUdp: 'Particion'}`. Doc 93 D10: con el UDP declarado, las columnas de
partición SON las que tienen PART_nn en ese UDP (el flag `isPartition` de la
plataforma se ignora) y se ordenan por nn; van al final del CREATE y el
PARTITIONED BY lleva solo los nombres. `partitionOrderUdp` es alias legado.
Sin UDP (export nativo) rige el flag. Validación, pipeline y /test."""
from __future__ import annotations

import asyncio
import re
from unittest.mock import AsyncMock

from app.features.ddl_rules.engine import generators as g
from app.features.ddl_rules.engine import pipeline
from app.features.ddl_rules.engine import validate as v
from app.features.ddl_rules.engine.context import column_ctx, names_by_id, table_ctx
from app.features.ddl_rules.templates import SEED_RULES, TEMPLATES

LAYOUT = {"id": "l1", "name": "particiones_al_final", "kind": "rule", "target": "table",
          "condition": "", "action": {"layout": {"partitionColumns": "last"}},
          "appliesTo": ["ddl.tabla_fisica"], "priority": 30, "enabled": True, "validationState": "valid"}
LAYOUT_UDP = {**LAYOUT, "action": {"layout": {"partitionColumns": "last", "partitionUdp": "Particion"}}}
LEGACY_UDP = {**LAYOUT, "action": {"layout": {"partitionColumns": "last", "partitionOrderUdp": "Particion"}}}
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


def _body_names(sql: str) -> list[str]:
    """Nombres de columna del CREATE generado, sin comillas (formato-agnóstico)."""
    body = sql.split("(\n", 1)[1].split("\n)", 1)[0]
    return [line.split()[0].strip("`") for line in body.split(",\n")]


def _partitioned_by(sql: str) -> list[str]:
    """Nombres del PARTITIONED BY (doc 107: cada uno con su tipo)."""
    m = re.search(r"PARTITIONED BY \(([^)]*)\)", sql)
    return [p.strip().split()[0].strip("`") for p in m.group(1).split(",")] if m else []


def test_partition_correlative():
    assert g.partition_correlative("PART_01") == 1 and g.partition_correlative("part-12") == 12
    assert g.partition_correlative("No Definido") is None and g.partition_correlative(None) is None


def test_partition_udp_of_acepta_la_clave_nueva_y_el_alias_legado():
    assert g.partition_udp_of({"partitionUdp": " Particion "}) == "Particion"
    assert g.partition_udp_of({"partitionOrderUdp": "Particion"}) == "Particion"
    assert g.partition_udp_of({"partitionUdp": "A", "partitionOrderUdp": "B"}) == "A"
    assert g.partition_udp_of({}) is None and g.partition_udp_of(None) is None


def test_layout_from_rules_solo_reglas_de_tabla_sobre_la_fisica():
    assert g.layout_from_rules([LAYOUT]) == {"partitionsLast": True, "partitionUdp": None}
    assert g.layout_from_rules([LAYOUT_UDP]) == {"partitionsLast": True, "partitionUdp": "Particion"}
    assert g.layout_from_rules([LEGACY_UDP]) == {"partitionsLast": True, "partitionUdp": "Particion"}
    off = {"partitionsLast": False, "partitionUdp": None}
    assert g.layout_from_rules([{**LAYOUT, "appliesTo": ["ddl.tabla_rej"]}]) == off
    assert g.layout_from_rules([{**LAYOUT, "action": {"layout": {"partitionColumns": "keep"}}}]) == off
    assert g.layout_from_rules([REJ]) == off


def test_layout_columns_estable_y_sin_opcion_intacto():
    cols = [{"name": "a", "partition": True}, {"name": "b"}, {"name": "c", "partition": True}, {"name": "d"}]
    assert [c["name"] for c in g.layout_columns(cols, {"partitionsLast": True})] == ["b", "d", "a", "c"]
    assert [c["name"] for c in g.layout_columns(cols, None)] == ["a", "b", "c", "d"]
    cols2 = [{"name": "a", "partition": True, "partitionOrder": 2}, {"name": "b"},
             {"name": "c", "partition": True, "partitionOrder": None},
             {"name": "d", "partition": True, "partitionOrder": 1}]
    assert [c["name"] for c in g.layout_columns(cols2, {"partitionsLast": True})] == ["b", "d", "a", "c"]


def test_sin_udp_rige_el_flag_y_el_orden_fisico():
    shuffled = [COLS[2], COLS[0], COLS[3], COLS[1]]         # entrada desordenada: se ordena por `ordinal`
    stmts, _ = g.run_generators([REJ], TABLE, shuffled, BASE, CTX, CONFIG, {})
    sql = stmts[0]["sql"]
    assert _body_names(sql) == ["cod_cli", "monto"]                  # doc 107: particiones fuera de la lista
    assert _partitioned_by(sql) == ["codmes", "fecdia"]
    off, _ = g.run_generators([REJ], TABLE, shuffled, BASE, CTX, CONFIG, {"includePartitions": False})
    assert _body_names(off[0]["sql"]) == ["cod_cli", "monto", "codmes", "fecdia"]   # sin PARTITIONED BY, al final


def test_con_udp_las_particiones_se_ordenan_por_part_nn():
    stmts, _ = g.run_generators([REJ, LAYOUT_UDP], TABLE, COLS, BASE, CTX, CONFIG, {})
    sql = stmts[0]["sql"]
    assert _body_names(sql) == ["cod_cli", "monto"]
    assert _partitioned_by(sql) == ["fecdia", "codmes"]
    off, _ = g.run_generators([REJ, LAYOUT_UDP], TABLE, COLS, BASE, CTX, CONFIG, {"includePartitions": False})
    assert _body_names(off[0]["sql"]) == ["cod_cli", "monto", "fecdia", "codmes"]


def test_con_udp_las_particiones_salen_del_udp_y_no_del_flag():
    """Doc 93 D10: con el UDP declarado, es partición la columna con PART_nn —
    aunque el flag diga lo contrario."""
    cols = [
        {"physicalName": "codmes", "dataType": "INT", "ordinal": 0, "isPartition": True,
         "udpValues": {"u-part": "No Definido"}},
        {"physicalName": "cod_cli", "dataType": "STRING", "ordinal": 1, "udpValues": {}},
        {"physicalName": "fecdia", "dataType": "DATE", "ordinal": 2, "isPartition": False,
         "udpValues": {"u-part": "PART_01"}},
    ]
    ctx = {c["physicalName"]: column_ctx(c, N) for c in cols}
    lights = g.physical_columns(cols, ctx, "Particion")
    assert [(c["name"], c["partition"], c["partitionOrder"]) for c in lights] == [
        ("codmes", False, None), ("cod_cli", False, None), ("fecdia", True, 1)]
    assert [c["partition"] for c in g.physical_columns(cols, ctx, None)] == [True, False, False]
    stmts, _ = g.run_generators([REJ, LAYOUT_UDP], TABLE, cols, BASE, ctx, CONFIG, {})
    assert _partitioned_by(stmts[0]["sql"]) == ["fecdia"]


def test_physical_columns_usa_el_ddltype_del_front():
    cols = [{"physicalName": "a", "dataType": "INTEGER", "ddlType": "int", "ordinal": 0, "udpValues": {}}]
    assert g.physical_columns(cols, {}, None)[0]["type"] == "int"
    assert g.physical_columns([{**cols[0], "ddlType": None}], {}, None)[0]["type"] == "INTEGER"


def test_pipeline_deriva_el_layout_de_las_reglas_que_corren():
    payload = {"model": {"name": "DDV", "udpValues": {}}, "options": {},
               "tables": [{"table": TABLE, "columns": COLS, "baseSql": "CREATE TABLE core.tbl_x (x INT);"}],
               "views": []}
    out = pipeline.render_export(payload, [LAYOUT, REJ], CONFIG, [], {})
    assert [s["artifact"] for s in out["statements"]] == ["ddl.tabla_fisica", "ddl.tabla_rej"]
    # doc 107: con PARTITIONED BY las particiones van ahí; `last` solo cuenta sin él
    applied = [e for e in out["log"] if e.get("rule") == "particiones_al_final"]
    assert applied and applied[0]["status"] == "skipped" and "PARTITIONED BY" in applied[0]["reason"]
    out_off = pipeline.render_export({**payload, "options": {"includePartitions": False}}, [LAYOUT, REJ], CONFIG, [], {})
    off = [e for e in out_off["log"] if e.get("rule") == "particiones_al_final"]
    assert off and off[0]["status"] == "applied" and "partition columns last" in off[0]["object"]
    out_udp = pipeline.render_export(payload, [LAYOUT_UDP, REJ], CONFIG, DEFS, {})
    applied_udp = [e for e in out_udp["log"] if e.get("rule") == "particiones_al_final"]
    assert "partitions from UDP 'Particion'" in applied_udp[0]["object"]
    out2 = pipeline.render_export(payload, [{**LAYOUT, "enabled": False}, REJ], CONFIG, [], {})
    assert [e["status"] for e in out2["log"] if e.get("rule") == "particiones_al_final"] == ["skipped"]


def test_udp_de_particiones_inexistente_cae_al_flag_y_lo_registra():
    payload = {"model": {"name": "DDV", "udpValues": {}}, "options": {},
               "tables": [{"table": TABLE, "columns": COLS, "baseSql": "CREATE TABLE core.tbl_x (x INT);"}],
               "views": []}
    out = pipeline.render_export(payload, [LAYOUT_UDP, REJ], CONFIG, [], {})    # sin defs: 'Particion' no existe
    rej = next(s["sql"] for s in out["statements"] if s["artifact"] == "ddl.tabla_rej")
    assert _partitioned_by(rej) == ["codmes", "fecdia"]                           # flag, orden físico
    assert any(e["status"] == "skipped" and "Particion" in (e.get("reason") or "") for e in out["log"])


def test_udp_de_particiones_que_no_es_de_columna_fisica_cae_al_flag():
    """Final review #7: el motor usa la MISMA regla que el front
    (`withUdpPartitions`): solo una def de COLUMNA y física define las
    particiones; una homónima de tabla o lógica no — si no, la física (front)
    saldría por flag y la _rej (motor) por UDP."""
    payload = {"model": {"name": "DDV", "udpValues": {}}, "options": {},
               "tables": [{"table": TABLE, "columns": COLS, "baseSql": "CREATE TABLE core.tbl_x (x INT);"}],
               "views": []}
    for other in ({**DEFS[0], "level": "table"}, {**DEFS[0], "view": "logical"}):
        out = pipeline.render_export(payload, [LAYOUT_UDP, REJ], CONFIG, [other], {})
        rej = next(st["sql"] for st in out["statements"] if st["artifact"] == "ddl.tabla_rej")
        assert _partitioned_by(rej) == ["codmes", "fecdia"], other                 # flag, orden físico
        assert any(e["status"] == "skipped" and "Particion" in (e.get("reason") or "") for e in out["log"]), other


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


def test_validate_partition_udp_y_alias_enlazan_el_udp_o_fallan():
    for rule in (LAYOUT_UDP, LEGACY_UDP):
        ok = v.validate_rule(rule, DEFS, {}, ARTS, {"ddl.tabla_fisica": "table"})
        assert ok["state"] == "valid" and ok["udpRefs"] == [{"udpId": "u-part", "level": "column"}]
    bad = v.validate_rule({**LAYOUT_UDP, "action": {"layout": {"partitionColumns": "last",
                                                               "partitionUdp": "Particio"}}},
                          DEFS, {}, ARTS, {})
    assert bad["state"] == "invalid"
    err = next(e for e in bad["errors"] if e["check"] == "UDP exists in catalog")
    assert "Particio" in err["message"] and err["suggestion"] == "Particion"
    vacio = v.validate_rule({**LAYOUT_UDP, "action": {"layout": {"partitionUdp": ""}}}, DEFS, {}, ARTS, {})
    assert vacio["state"] == "invalid"


def test_semilla_y_plantilla_de_layout():
    seed = next(r for r in SEED_RULES if r["name"] == "particiones_al_final")
    assert g.partition_udp_of(seed["action"]["layout"]) == "Particion"
    assert seed["action"]["layout"]["partitionColumns"] == "last"
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
    assert frag["sql"] == "-- column order: cod_cli, monto\n-- PARTITIONED BY (codmes int, fecdia date)"
    assert frag["why"] == "partition columns only in PARTITIONED BY: codmes, fecdia"
    out_udp = asyncio.run(svc.test_rule("p1", LAYOUT_UDP, "t1"))
    frag_udp = out_udp["fragments"][0]
    assert frag_udp["sql"] == "-- column order: cod_cli, monto\n-- PARTITIONED BY (fecdia date, codmes int)"
    assert frag_udp["why"] == "partition columns only in PARTITIONED BY: fecdia, codmes · from UDP 'Particion'"


def test_doc94_orden_unico_pk_primero_aunque_el_ordinal_este_desordenado():
    """Doc 94 D5: la `_rej` (espejo de la física) sigue el orden único — PK
    primero y cada bloque por ordinal —, igual que el CREATE del front."""
    legacy = [
        {"physicalName": "dato", "dataType": "STRING", "ordinal": 0, "udpValues": {}},
        {"physicalName": "clave", "dataType": "STRING", "ordinal": 5, "isPrimaryKey": True, "udpValues": {}},
    ]
    stmts, _ = g.run_generators([REJ], TABLE, legacy, BASE, CTX, CONFIG, {})
    assert _body_names(stmts[0]["sql"]) == ["clave", "dato"]
