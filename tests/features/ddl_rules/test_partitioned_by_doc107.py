"""Doc 107 · Particiones solo en el PARTITIONED BY — formato FIJO del DDL, no una
regla (corrección del owner, 2026-10-01): cuando el export escribe PARTITIONED BY,
las columnas de partición NO se declaran en la lista del CREATE TABLE; van dentro
del PARTITIONED BY con su tipo (`PARTITIONED BY (fecdia date, codmes int)`), en el
orden del UDP (PART_01, PART_02…) cuando la regla de layout lo declara, si no en
orden físico. Igual en la física (la arma el front, con o sin reglas) y en las
tablas generadas (la `_rej`); la regla de tipos (`char_a_varchar`) también
reescribe los tipos que quedan dentro del PARTITIONED BY. Sin PARTITIONED BY
(Output setting «Partitions» apagada) las particiones vuelven a la lista: al
final con `partitionColumns: last`, como antes."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.ddl_rules.engine import generators as g
from app.features.ddl_rules.engine import pipeline, render
from app.features.ddl_rules.engine import validate as v
from app.features.ddl_rules.engine.context import column_ctx, names_by_id, table_ctx
from app.features.ddl_rules.templates import SEED_RULES, TEMPLATES

LAYOUT = {"id": "l1", "name": "particiones_al_final", "kind": "rule", "target": "table",
          "condition": "", "action": {"layout": {"partitionColumns": "last", "partitionUdp": "Particion"}},
          "appliesTo": ["ddl.tabla_fisica"], "priority": 30, "enabled": True, "validationState": "valid"}
REJ = {"id": "g1", "name": "tabla_rechazos", "kind": "generator", "sourceArtifact": "ddl.tabla_fisica",
       "condition": "", "priority": 200, "enabled": True, "validationState": "valid",
       "action": {"emit": {"artifact": "ddl.tabla_rej", "type": "table", "schema": "{tabla.esquema}",
                           "name": "{tabla.nombre}_rej",
                           "columns": {"inherit": "all", "force_type": "STRING", "keep_partition_type": True,
                                       "strip": ["not_null", "pk"]},
                           "add_columns": [{"name": "tiporeject", "type": "STRING"}]}}}
CHAR_VARCHAR = {"id": "ty1", "name": "char_a_varchar", "kind": "rule", "target": "column",
                "condition": "", "action": {"types": {"CHAR": "VARCHAR"}},
                "appliesTo": ["ddl.tabla_fisica", "ddl.tabla_rej"], "priority": 80,
                "enabled": True, "validationState": "valid"}
TABLE = {"id": "t1", "physicalName": "tbl_x", "schema": "core", "udpValues": {}}
DEFS = [{"id": "u-part", "name": "Particion", "level": "column", "dataType": "list",
         "allowedValues": ["No Definido", "PART_01", "PART_02", "PART_03"]}]
# Orden físico: codmes (PART_02) primero; fecdia (PART_01) en el medio; tipo (PART_03) al final.
COLS = [
    {"physicalName": "codmes", "dataType": "INT", "ordinal": 0, "isPartition": True, "isNullable": False,
     "udpValues": {"u-part": "PART_02"}},
    {"physicalName": "cod_cli", "dataType": "STRING", "ordinal": 1, "isPrimaryKey": True, "isNullable": False,
     "udpValues": {}},
    {"physicalName": "fecdia", "dataType": "DATE", "ordinal": 2, "isPartition": True, "udpValues": {"u-part": "PART_01"}},
    {"physicalName": "monto", "dataType": "DECIMAL(18,2)", "ordinal": 3, "udpValues": {}},
    {"physicalName": "tipo", "dataType": "CHAR(1)", "ordinal": 4, "isPartition": True, "udpValues": {"u-part": "PART_03"}},
]
N = names_by_id(DEFS)
BASE = {"tabla": table_ctx(TABLE, N), "modelo": {"nombre": "DDV", "udp": {}}}
CTX = {c["physicalName"]: column_ctx(c, N) for c in COLS}
CONFIG = {"lookups": {}, "functions": []}
# CREATE físico tal como lo emite el FRONT (con o sin reglas).
BASE_SQL = ("CREATE OR REPLACE TABLE core.tbl_x (\n  cod_cli string,\n  monto decimal(18,2)\n)\nUSING DELTA\n"
            "PARTITIONED BY (fecdia date, codmes int, tipo char(1) COMMENT 'char(1), (x)')\n"
            "LOCATION 'abfss://x/TBL_X';")
ARTS = ["ddl.tabla_fisica", "ddl.vista_negocio", "ddl.tabla_rej"]
REJ_ON = ("CREATE OR REPLACE TABLE core.tbl_x_rej (\n"
          "  cod_cli string,\n  monto string,\n  tiporeject string\n)\n"
          "USING DELTA\n")


def _rej_sql(rules, options=None) -> str:
    stmts, _ = g.run_generators(rules, TABLE, COLS, BASE, CTX, CONFIG, options or {})
    return next(s["sql"] for s in stmts if s["artifact"] == "ddl.tabla_rej")


def test_rej_declara_las_particiones_solo_en_partitioned_by_con_su_tipo():
    assert _rej_sql([REJ, LAYOUT]) == REJ_ON + "PARTITIONED BY (fecdia date, codmes int, tipo char(1));"


def test_no_hace_falta_una_regla_sin_layout_rige_el_flag_y_el_orden_fisico():
    assert _rej_sql([REJ]) == REJ_ON + "PARTITIONED BY (codmes int, fecdia date, tipo char(1));"


def test_rej_sin_partitioned_by_deja_las_particiones_al_final_de_la_lista():
    assert _rej_sql([REJ, LAYOUT], {"includePartitions": False}) == (
        "CREATE OR REPLACE TABLE core.tbl_x_rej (\n"
        "  cod_cli string,\n  monto string,\n  tiporeject string,\n"
        "  fecdia date,\n  codmes int,\n  tipo char(1)\n)\n"
        "USING DELTA;")


def test_rej_la_regla_de_tipos_alcanza_a_las_particiones():
    assert _rej_sql([REJ, LAYOUT, CHAR_VARCHAR]).endswith("PARTITIONED BY (fecdia date, codmes int, tipo varchar(1));")


def test_la_definicion_de_la_particion_se_mueve_completa_al_partitioned_by():
    """Con «Primary & foreign keys» la columna NOT NULL lo conserva dentro del
    PARTITIONED BY: la definición es la misma, solo cambia de lugar."""
    art = {"kind": "table", "schema": "s", "name": "t", "strip": set(),
           "columns": [{"name": "a", "type": "INT", "partition": False},
                       {"name": "p", "type": "DATE", "partition": True, "partitionOrder": 1}]}
    sql = g.artifact_sql(art, {"p": {"nullable": False}}, {"includeKeys": True})
    assert sql == "CREATE OR REPLACE TABLE s.t (\n  a int\n)\nUSING DELTA\nPARTITIONED BY (p date NOT NULL);"


def test_partitions_in_clause_depende_solo_del_partitioned_by():
    cols = [{"name": "a", "partition": True}, {"name": "b"}]
    assert g.partitions_in_clause(cols, {}) is True
    assert g.partitions_in_clause(cols, {"includePartitions": False}) is False
    assert g.partitions_in_clause([{"name": "b"}], {}) is False


def test_layout_from_rules_ya_no_tiene_otro_modo():
    assert g.layout_from_rules([LAYOUT]) == {"partitionsLast": True, "partitionUdp": "Particion"}


# ── La regla de tipos dentro del PARTITIONED BY (render) ───────────────────


def test_la_regla_de_tipos_tambien_reescribe_los_tipos_del_partitioned_by():
    out, log = render.apply_column_types_sql(BASE_SQL, [CHAR_VARCHAR], "ddl.tabla_fisica", BASE, CTX, CONFIG)
    assert out == BASE_SQL.replace("tipo char(1) COMMENT", "tipo varchar(1) COMMENT")
    assert [e["column"] for e in log if e["status"] == "applied"] == ["tipo"]


def test_partitioned_by_solo_con_nombres_no_se_toca():
    sql = ("CREATE OR REPLACE TABLE core.tbl_x (\n  cod_cli string,\n  tipo char(1)\n)\nUSING DELTA\n"
           "PARTITIONED BY (tipo)\nLOCATION 'abfss://x/TBL_X';")
    out, _ = render.apply_column_types_sql(sql, [CHAR_VARCHAR], "ddl.tabla_fisica", BASE, CTX, CONFIG)
    assert out == sql.replace("  tipo char(1)", "  tipo varchar(1)")


def test_una_columna_llamada_partitioned_by_no_esconde_la_clausula():
    """sqlglot lee `partitioned_by` sin comillas como el token PARTITIONED BY: la
    cláusula real se busca DESPUÉS de la lista de columnas."""
    sql = ("CREATE OR REPLACE TABLE core.tbl_x (\n  partitioned_by string,\n  cod_cli string\n)\nUSING DELTA\n"
           "PARTITIONED BY (tipo char(1))\nLOCATION 'abfss://x/TBL_X';")
    out, _ = render.apply_column_types_sql(sql, [CHAR_VARCHAR], "ddl.tabla_fisica", BASE, CTX, CONFIG)
    assert out == sql.replace("tipo char(1)", "tipo varchar(1)")


def test_un_partitioned_by_dentro_de_la_lista_no_se_reescribe_dos_veces():
    sql = "CREATE TABLE t (tipo CHAR(1) PARTITIONED BY (codmes CHAR(3)), cod_cli int);"
    out, _ = render.apply_column_types_sql(sql, [CHAR_VARCHAR], "ddl.tabla_fisica", BASE, CTX, CONFIG)
    assert "VARCHARHAR" not in out and out.count("VARCHAR(") == 2


def test_el_partitioned_by_de_otro_statement_no_se_toca():
    sql = "CREATE TABLE t (cod_cli CHAR(1)) USING DELTA;\nCREATE TABLE u PARTITIONED BY (tipo CHAR(1));"
    out, _ = render.apply_column_types_sql(sql, [CHAR_VARCHAR], "ddl.tabla_fisica", BASE, CTX, CONFIG)
    assert out == sql.replace("cod_cli CHAR(1)", "cod_cli VARCHAR(1)")


# ── Log del export, validación y semilla ───────────────────────────────────


def _payload(options):
    return {"model": {"name": "DDV", "udpValues": {}}, "options": options,
            "tables": [{"table": TABLE, "columns": COLS, "baseSql": BASE_SQL}], "views": []}


def _layout_entries(out):
    return [e for e in out["log"] if e.get("rule") == "particiones_al_final"]


def test_el_log_de_la_regla_de_layout_dice_lo_que_sale():
    out = pipeline.render_export(_payload({}), [LAYOUT, REJ], CONFIG, DEFS, {})
    assert _layout_entries(out) == [{"rule": "particiones_al_final", "status": "applied", "artifact": "ddl.tabla_fisica",
                                     "object": "column layout: partitions from UDP 'Particion'"}]
    rej = next(s["sql"] for s in out["statements"] if s["artifact"] == "ddl.tabla_rej")
    assert rej.endswith("PARTITIONED BY (fecdia date, codmes int, tipo char(1));")
    off = pipeline.render_export(_payload({"includePartitions": False}), [LAYOUT, REJ], CONFIG, DEFS, {})
    assert _layout_entries(off)[0]["object"] == "column layout: partition columns last · partitions from UDP 'Particion'"
    # `last` sin UDP y con PARTITIONED BY no cambia nada: queda como saltada, con el porqué
    sin_udp = {**LAYOUT, "action": {"layout": {"partitionColumns": "last"}}}
    out2 = pipeline.render_export(_payload({}), [sin_udp, REJ], CONFIG, DEFS, {})
    assert _layout_entries(out2) == [{"rule": "particiones_al_final", "status": "skipped", "artifact": "ddl.tabla_fisica",
                                      "reason": "the partition columns go in PARTITIONED BY; 'last' only applies "
                                                "when the export doesn't write it"}]


def test_validate_partition_columns_sigue_siendo_keep_o_last():
    assert v.validate_rule(LAYOUT, DEFS, {}, ARTS, {"ddl.tabla_fisica": "table"})["state"] == "valid"
    bad = v.validate_rule({**LAYOUT, "action": {"layout": {"partitionColumns": "partitioned-by"}}}, DEFS, {}, ARTS, {})
    assert "must be one of: keep, last" in " | ".join(e["message"] for e in bad["errors"])


def test_semilla_y_plantilla_de_layout():
    seed = next(r for r in SEED_RULES if r["name"] == "particiones_al_final")
    assert seed["action"] == {"layout": {"partitionColumns": "last", "partitionUdp": "Particion"}}
    assert "PARTITIONED BY con su tipo" in seed["description"]
    tpl = next(t for t in TEMPLATES if t["id"] == "partitions-last")
    assert tpl["rule"] is seed and "PARTITIONED BY" in tpl["summary"]


# ── «Test» de la regla en Data Standards ───────────────────────────────────


def _bench(monkeypatch, saved_rules, output=None):
    from app.features.ddl_rules import service as svc
    monkeypatch.setattr(svc.udp_repo, "list_udp", AsyncMock(return_value=DEFS))
    monkeypatch.setattr(svc.repository, "get_config", AsyncMock(return_value={
        "id": "global", "lookups": {}, "functions": [], **({"output": output} if output else {})}))
    monkeypatch.setattr(svc.repository, "list_rules", AsyncMock(return_value=saved_rules))
    monkeypatch.setattr(svc.dom_repo, "list_domains", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc.catalog_repo, "list_tables_by_ids", AsyncMock(return_value=[TABLE]))
    monkeypatch.setattr(svc.catalog_repo, "list_columns", AsyncMock(return_value=COLS))
    return svc


def test_el_test_de_la_regla_de_layout(monkeypatch):
    svc = _bench(monkeypatch, [])
    frag = asyncio.run(svc.test_rule("p1", LAYOUT, "t1"))["fragments"][0]
    assert frag["sql"] == ("-- column order: cod_cli, monto\n"
                           "-- PARTITIONED BY (fecdia date, codmes int, tipo char(1))")
    assert frag["why"] == "partition columns only in PARTITIONED BY: fecdia, codmes, tipo · from UDP 'Particion'"


def test_el_test_sin_partitioned_by(monkeypatch):
    svc = _bench(monkeypatch, [], output={"includePartitions": False})
    frag = asyncio.run(svc.test_rule("p1", LAYOUT, "t1"))["fragments"][0]
    assert frag["sql"] == "-- column order: cod_cli, monto, fecdia, codmes, tipo"
    assert frag["why"] == ("partition columns last (this export has no PARTITIONED BY): fecdia, codmes, tipo"
                           " · from UDP 'Particion'")
    keep = {**LAYOUT, "action": {"layout": {"partitionColumns": "keep", "partitionUdp": "Particion"}}}
    frag = asyncio.run(svc.test_rule("p1", keep, "t1"))["fragments"][0]
    assert frag["sql"] == "-- column order: cod_cli, codmes, fecdia, monto, tipo"
    assert frag["why"] == ("partition columns in physical order (this export has no PARTITIONED BY): fecdia, codmes, tipo"
                           " · from UDP 'Particion'")


def test_el_test_de_un_generador_usa_el_layout_del_proyecto(monkeypatch):
    svc = _bench(monkeypatch, [LAYOUT, REJ])
    sql = asyncio.run(svc.test_rule("p1", REJ, "t1"))["fragments"][0]["sql"]
    assert "  tiporeject string\n)\nUSING DELTA\nPARTITIONED BY (fecdia date, codmes int, tipo char(1))" in sql
