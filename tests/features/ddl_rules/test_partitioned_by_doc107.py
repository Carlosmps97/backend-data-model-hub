"""Doc 107 · Particiones solo en el PARTITIONED BY: con la regla de layout
`partitionColumns: 'partitioned-by'` las columnas de partición NO se declaran en
la lista del CREATE TABLE — van dentro del PARTITIONED BY con su tipo
(`PARTITIONED BY (fecdia date, codmes int)`), en el orden del UDP (PART_01,
PART_02…). Mismo formato en la física (la arma el front) y en las tablas
generadas (la `_rej`); la regla de tipos (`char_a_varchar`) también reescribe
los tipos que quedan dentro del PARTITIONED BY. Sin PARTITIONED BY (Output
setting «Partitions» apagada) las particiones no desaparecen: vuelven al final
de la lista. `last` y `keep` siguen igual. Semilla: `particiones_en_partitioned_by`."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.ddl_rules.engine import generators as g
from app.features.ddl_rules.engine import pipeline, render
from app.features.ddl_rules.engine import validate as v
from app.features.ddl_rules.engine.context import column_ctx, names_by_id, table_ctx
from app.features.ddl_rules.templates import SEED_RULES, TEMPLATES

CLAUSE = {"id": "l1", "name": "particiones_en_partitioned_by", "kind": "rule", "target": "table",
          "condition": "", "action": {"layout": {"partitionColumns": "partitioned-by", "partitionUdp": "Particion"}},
          "appliesTo": ["ddl.tabla_fisica"], "priority": 30, "enabled": True, "validationState": "valid"}
LAST = {**CLAUSE, "name": "particiones_al_final",
        "action": {"layout": {"partitionColumns": "last", "partitionUdp": "Particion"}}}
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
# CREATE físico tal como lo emite el FRONT con la regla nueva.
BASE_SQL = ("CREATE OR REPLACE TABLE core.tbl_x (\n  cod_cli string,\n  monto decimal(18,2)\n)\nUSING DELTA\n"
            "PARTITIONED BY (fecdia date, codmes int, tipo char(1) COMMENT 'char(1), (x)')\n"
            "LOCATION 'abfss://x/TBL_X';")
ARTS = ["ddl.tabla_fisica", "ddl.vista_negocio", "ddl.tabla_rej"]


def _rej_sql(rules, options=None) -> str:
    stmts, _ = g.run_generators(rules, TABLE, COLS, BASE, CTX, CONFIG, options or {})
    return next(s["sql"] for s in stmts if s["artifact"] == "ddl.tabla_rej")


def test_layout_from_rules_reconoce_partitioned_by():
    assert g.layout_from_rules([CLAUSE]) == {"partitionsLast": False, "partitionsInClause": True,
                                             "partitionUdp": "Particion"}
    assert g.layout_from_rules([LAST]) == {"partitionsLast": True, "partitionsInClause": False,
                                           "partitionUdp": "Particion"}
    assert g.layout_from_rules([{**CLAUSE, "appliesTo": ["ddl.tabla_rej"]}])["partitionsInClause"] is False
    assert g.layout_from_rules([])["partitionsInClause"] is False


def test_layout_columns_en_partitioned_by_saca_las_particiones_de_la_lista():
    cols = [{"name": "a", "partition": True, "partitionOrder": 2}, {"name": "b"},
            {"name": "c", "partition": True, "partitionOrder": 1}, {"name": "d"}]

    def names(o):
        return [c["name"] for c in g.layout_columns(cols, o)]

    assert names({"partitionsInClause": True}) == ["b", "d"]
    # sin PARTITIONED BY (Output setting apagada) no pueden desaparecer: al final, en orden PART_nn
    assert names({"partitionsInClause": True, "includePartitions": False}) == ["b", "d", "c", "a"]
    # 'last' y 'keep' igual que antes
    assert names({"partitionsLast": True}) == ["b", "d", "c", "a"]
    assert names(None) == ["a", "b", "c", "d"]
    sin = [{"name": "x"}, {"name": "y"}]
    assert [c["name"] for c in g.layout_columns(sin, {"partitionsInClause": True})] == ["x", "y"]


def test_rej_declara_las_particiones_solo_en_partitioned_by_con_su_tipo():
    assert _rej_sql([REJ, CLAUSE]) == (
        "CREATE OR REPLACE TABLE core.tbl_x_rej (\n"
        "  cod_cli string,\n  monto string,\n  tiporeject string\n)\n"
        "USING DELTA\n"
        "PARTITIONED BY (fecdia date, codmes int, tipo char(1));")


def test_rej_con_last_sigue_igual_que_antes():
    assert _rej_sql([REJ, LAST]) == (
        "CREATE OR REPLACE TABLE core.tbl_x_rej (\n"
        "  cod_cli string,\n  monto string,\n  tiporeject string,\n"
        "  fecdia date,\n  codmes int,\n  tipo char(1)\n)\n"
        "USING DELTA\n"
        "PARTITIONED BY (fecdia, codmes, tipo);")


def test_rej_sin_partitioned_by_deja_las_particiones_al_final_de_la_lista():
    assert _rej_sql([REJ, CLAUSE], {"includePartitions": False}) == (
        "CREATE OR REPLACE TABLE core.tbl_x_rej (\n"
        "  cod_cli string,\n  monto string,\n  tiporeject string,\n"
        "  fecdia date,\n  codmes int,\n  tipo char(1)\n)\n"
        "USING DELTA;")


def test_rej_la_regla_de_tipos_alcanza_a_las_particiones():
    assert "PARTITIONED BY (fecdia date, codmes int, tipo varchar(1));" in _rej_sql([REJ, CLAUSE, CHAR_VARCHAR])


def test_la_definicion_de_la_particion_se_mueve_completa_al_partitioned_by():
    """Con «Primary & foreign keys» la columna NOT NULL lo conserva dentro del
    PARTITIONED BY: la definición es la misma, solo cambia de lugar."""
    art = {"kind": "table", "schema": "s", "name": "t", "strip": set(),
           "columns": [{"name": "a", "type": "INT", "partition": False},
                       {"name": "p", "type": "DATE", "partition": True, "partitionOrder": 1}]}
    sql = g.artifact_sql(art, {"p": {"nullable": False}}, {"includeKeys": True, "partitionsInClause": True})
    assert sql == "CREATE OR REPLACE TABLE s.t (\n  a int\n)\nUSING DELTA\nPARTITIONED BY (p date NOT NULL);"


def test_la_regla_de_tipos_tambien_reescribe_los_tipos_del_partitioned_by():
    out, log = render.apply_column_types_sql(BASE_SQL, [CHAR_VARCHAR], "ddl.tabla_fisica", BASE, CTX, CONFIG)
    assert out == BASE_SQL.replace("tipo char(1) COMMENT", "tipo varchar(1) COMMENT")
    assert [e["column"] for e in log if e["status"] == "applied"] == ["tipo"]


def test_partitioned_by_solo_con_nombres_no_se_toca():
    sql = ("CREATE OR REPLACE TABLE core.tbl_x (\n  cod_cli string,\n  tipo char(1)\n)\nUSING DELTA\n"
           "PARTITIONED BY (tipo)\nLOCATION 'abfss://x/TBL_X';")
    out, _ = render.apply_column_types_sql(sql, [CHAR_VARCHAR], "ddl.tabla_fisica", BASE, CTX, CONFIG)
    assert out == sql.replace("  tipo char(1)", "  tipo varchar(1)")


def test_pipeline_registra_el_layout_nuevo():
    payload = {"model": {"name": "DDV", "udpValues": {}}, "options": {},
               "tables": [{"table": TABLE, "columns": COLS, "baseSql": BASE_SQL}], "views": []}
    out = pipeline.render_export(payload, [CLAUSE, REJ], CONFIG, DEFS, {})
    applied = [e for e in out["log"] if e.get("rule") == "particiones_en_partitioned_by"]
    assert applied and applied[0]["status"] == "applied"
    assert applied[0]["object"] == ("column layout: partition columns only in PARTITIONED BY · "
                                    "partitions from UDP 'Particion'")
    rej = next(s["sql"] for s in out["statements"] if s["artifact"] == "ddl.tabla_rej")
    assert rej.endswith("PARTITIONED BY (fecdia date, codmes int, tipo char(1));")


def test_validate_acepta_partitioned_by():
    ok = v.validate_rule(CLAUSE, DEFS, {}, ARTS, {"ddl.tabla_fisica": "table"})
    assert ok["state"] == "valid" and ok["errors"] == []
    bad = v.validate_rule({**CLAUSE, "action": {"layout": {"partitionColumns": "first"}}}, DEFS, {}, ARTS, {})
    assert "must be one of: keep, last, partitioned-by" in " | ".join(e["message"] for e in bad["errors"])


def test_semilla_y_plantilla_usan_partitioned_by():
    names = {r["name"] for r in SEED_RULES}
    assert "particiones_al_final" not in names
    seed = next(r for r in SEED_RULES if r["name"] == "particiones_en_partitioned_by")
    assert seed["action"] == {"layout": {"partitionColumns": "partitioned-by", "partitionUdp": "Particion"}}
    assert seed["condition"] == "" and seed["appliesTo"] == ["ddl.tabla_fisica"]
    assert v.validate_rule({**seed, "id": None, "udpRefs": []}, DEFS, {}, ARTS, {})["state"] == "valid"
    tpl = next(t for t in TEMPLATES if t["id"] == "partitions-in-partitioned-by")
    assert tpl["rule"] is seed and "PARTITIONED BY" in tpl["title"]
    assert not any(t["id"] == "partitions-last" for t in TEMPLATES)


def test_service_test_rule_layout_partitioned_by(monkeypatch):
    from app.features.ddl_rules import service as svc
    monkeypatch.setattr(svc.udp_repo, "list_udp", AsyncMock(return_value=DEFS))
    monkeypatch.setattr(svc.repository, "get_config",
                        AsyncMock(return_value={"id": "global", "lookups": {}, "functions": []}))
    monkeypatch.setattr(svc.repository, "list_rules", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc.dom_repo, "list_domains", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc.catalog_repo, "list_tables_by_ids", AsyncMock(return_value=[TABLE]))
    monkeypatch.setattr(svc.catalog_repo, "list_columns", AsyncMock(return_value=COLS))
    frag = asyncio.run(svc.test_rule("p1", CLAUSE, "t1"))["fragments"][0]
    assert frag["sql"] == ("-- column order: cod_cli, monto\n"
                           "-- PARTITIONED BY (fecdia date, codmes int, tipo char(1))")
    assert frag["why"] == "partition columns only in PARTITIONED BY: fecdia, codmes, tipo · from UDP 'Particion'"


# ── Revisión independiente del doc 107 ─────────────────────────────────────


def test_revision_una_columna_llamada_partitioned_by_no_esconde_la_clausula():
    """sqlglot lee `partitioned_by` sin comillas como el token PARTITIONED BY: la
    cláusula real se busca DESPUÉS de la lista de columnas."""
    sql = ("CREATE OR REPLACE TABLE core.tbl_x (\n  partitioned_by string,\n  cod_cli string\n)\nUSING DELTA\n"
           "PARTITIONED BY (tipo char(1))\nLOCATION 'abfss://x/TBL_X';")
    out, _ = render.apply_column_types_sql(sql, [CHAR_VARCHAR], "ddl.tabla_fisica", BASE, CTX, CONFIG)
    assert out == sql.replace("tipo char(1)", "tipo varchar(1)")


def test_revision_un_partitioned_by_dentro_de_la_lista_no_se_reescribe_dos_veces():
    sql = "CREATE TABLE t (tipo CHAR(1) PARTITIONED BY (codmes CHAR(3)), cod_cli int);"
    out, _ = render.apply_column_types_sql(sql, [CHAR_VARCHAR], "ddl.tabla_fisica", BASE, CTX, CONFIG)
    assert "VARCHARHAR" not in out and out.count("VARCHAR(") == 2


def test_revision_el_log_dice_lo_que_sale_y_cual_regla_de_layout_manda():
    payload = {"model": {"name": "DDV", "udpValues": {}}, "options": {"includePartitions": False},
               "tables": [{"table": TABLE, "columns": COLS, "baseSql": BASE_SQL}], "views": []}
    out = pipeline.render_export(payload, [CLAUSE, REJ], CONFIG, DEFS, {})
    entry = next(e for e in out["log"] if e.get("rule") == "particiones_en_partitioned_by")
    assert entry["object"] == ("column layout: partition columns last (this export has no PARTITIONED BY) · "
                               "partitions from UDP 'Particion'")
    # dos reglas de layout: la `partitioned-by` manda (front y motor) y el log lo dice
    old = {**LAST, "id": "l2", "action": {"layout": {"partitionColumns": "last"}}}
    out2 = pipeline.render_export({**payload, "options": {}}, [CLAUSE, old, REJ], CONFIG, DEFS, {})
    assert [e for e in out2["log"] if e.get("rule") == "particiones_al_final"] == [
        {"rule": "particiones_al_final", "status": "skipped", "artifact": "ddl.tabla_fisica",
         "reason": "another layout rule declares partition columns only in PARTITIONED BY"}]


def _bench(monkeypatch, saved_rules):
    from app.features.ddl_rules import service as svc
    monkeypatch.setattr(svc.udp_repo, "list_udp", AsyncMock(return_value=DEFS))
    monkeypatch.setattr(svc.repository, "get_config",
                        AsyncMock(return_value={"id": "global", "lookups": {}, "functions": []}))
    monkeypatch.setattr(svc.repository, "list_rules", AsyncMock(return_value=saved_rules))
    monkeypatch.setattr(svc.dom_repo, "list_domains", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc.catalog_repo, "list_tables_by_ids", AsyncMock(return_value=[TABLE]))
    monkeypatch.setattr(svc.catalog_repo, "list_columns", AsyncMock(return_value=COLS))
    return svc


def test_revision_el_test_de_un_generador_usa_el_layout_del_proyecto(monkeypatch):
    svc = _bench(monkeypatch, [CLAUSE, REJ])
    sql = asyncio.run(svc.test_rule("p1", REJ, "t1"))["fragments"][0]["sql"]
    assert "  tiporeject string\n)\nUSING DELTA\nPARTITIONED BY (fecdia date, codmes int, tipo char(1))" in sql
    assert "  tipo char(1)" not in sql


def test_revision_el_test_de_keep_no_dice_al_final(monkeypatch):
    svc = _bench(monkeypatch, [])
    keep = {**CLAUSE, "action": {"layout": {"partitionColumns": "keep", "partitionUdp": "Particion"}}}
    frag = asyncio.run(svc.test_rule("p1", keep, "t1"))["fragments"][0]
    assert frag["sql"] == "-- column order: cod_cli, codmes, fecdia, monto, tipo"
    assert frag["why"] == ("partition columns in physical order; PARTITIONED BY: fecdia, codmes, tipo"
                           " · from UDP 'Particion'")


# ── Revisión independiente, ronda 2 ────────────────────────────────────────


def test_revision2_el_partitioned_by_de_otro_statement_no_se_toca():
    sql = "CREATE TABLE t (cod_cli CHAR(1)) USING DELTA;\nCREATE TABLE u PARTITIONED BY (tipo CHAR(1));"
    out, _ = render.apply_column_types_sql(sql, [CHAR_VARCHAR], "ddl.tabla_fisica", BASE, CTX, CONFIG)
    assert out == sql.replace("cod_cli CHAR(1)", "cod_cli VARCHAR(1)")


def test_revision2_sin_partitioned_by_las_dos_reglas_de_layout_hacen_lo_mismo():
    payload = {"model": {"name": "DDV", "udpValues": {}}, "options": {"includePartitions": False},
               "tables": [{"table": TABLE, "columns": COLS, "baseSql": BASE_SQL}], "views": []}
    old = {**LAST, "id": "l2", "action": {"layout": {"partitionColumns": "last"}}}
    log = pipeline.render_export(payload, [CLAUSE, old, REJ], CONFIG, DEFS, {})["log"]
    assert [(e["rule"], e["status"]) for e in log if e.get("rule") in ("particiones_al_final", "particiones_en_partitioned_by")] \
        == [("particiones_en_partitioned_by", "applied"), ("particiones_al_final", "applied")]


def test_revision2_una_regla_last_con_el_udp_vigente_deja_una_sola_entrada():
    payload = {"model": {"name": "DDV", "udpValues": {}}, "options": {},
               "tables": [{"table": TABLE, "columns": COLS, "baseSql": BASE_SQL}], "views": []}
    old = {**LAST, "id": "l2", "priority": 90}          # corre primero y declara el mismo UDP
    log = [e for e in pipeline.render_export(payload, [CLAUSE, old, REJ], CONFIG, DEFS, {})["log"]
           if e.get("rule") == "particiones_al_final"]
    assert log == [{"rule": "particiones_al_final", "status": "applied", "artifact": "ddl.tabla_fisica",
                    "object": "column layout: partitions from UDP 'Particion' · partition columns: another "
                              "layout rule puts them only in PARTITIONED BY"}]


def test_revision2_el_test_considera_la_output_setting_partitions(monkeypatch):
    from app.features.ddl_rules import service as svc
    svc_ = _bench(monkeypatch, [])
    monkeypatch.setattr(svc_.repository, "get_config", AsyncMock(return_value={
        "id": "global", "lookups": {}, "functions": [], "output": {"includePartitions": False}}))
    keep = {**CLAUSE, "action": {"layout": {"partitionColumns": "keep", "partitionUdp": "Particion"}}}
    why = asyncio.run(svc.test_rule("p1", keep, "t1"))["fragments"][0]["why"]
    assert why == ("partition columns in physical order (this export has no PARTITIONED BY): fecdia, codmes, tipo"
                   " · from UDP 'Particion'")
    frag = asyncio.run(svc.test_rule("p1", CLAUSE, "t1"))["fragments"][0]
    assert frag["sql"] == "-- column order: cod_cli, monto, fecdia, codmes, tipo"
    assert frag["why"] == ("partition columns last (this export has no PARTITIONED BY): fecdia, codmes, tipo"
                           " · from UDP 'Particion'")
