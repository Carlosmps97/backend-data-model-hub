"""Doc 93 · estilo de salida del motor: sin comillas salvo nombres que las
necesitan, tipos en minúscula, CREATE OR REPLACE, USING en mayúscula, NOT NULL
solo con keys, ALTER TABLE para tags de vistas (ALTER VIEW por setting) y
regiones de columna robustas a nombres-palabra-clave sin comillas."""
from __future__ import annotations

import sqlglot

from app.features.ddl_rules.engine import generators as g
from app.features.ddl_rules.engine import render
from app.features.ddl_rules.engine.context import column_ctx, names_by_id, table_ctx

REJ = {"id": "g1", "name": "tabla_rechazos", "kind": "generator", "sourceArtifact": "ddl.tabla_fisica",
       "condition": "", "priority": 200, "enabled": True, "validationState": "valid",
       "action": {"emit": {"artifact": "ddl.tabla_rej", "type": "table", "schema": "{tabla.esquema}",
                           "name": "{tabla.nombre}_rej", "columns": {"inherit": "all"}}}}


def test_needs_quote_e_ident_segun_politica():
    assert not render.needs_quote("codmes") and not render.needs_quote("_x1")
    for name in ("hd baseconsolidada", "destipmodalidadpagocreditoÉrsonal", "1col", "a-b", "select", "FROM"):
        assert render.needs_quote(name), name
    assert render.ident("CODMES", {"identifierCase": "lower"}) == "codmes"
    assert render.ident("HD BASE", {"identifierCase": "lower"}) == "`hd base`"
    assert render.ident("codmes", {"quoteIdentifiers": "always"}) == "`codmes`"
    assert render.full_name("BCP_DDV", "HD_VENTA", {"identifierCase": "lower"}) == "bcp_ddv.hd_venta"
    assert render.artifact_ctx("BCP", "T", "table", {"identifierCase": "lower"})["ref"] == "bcp.t"
    assert render.proj_ident("codmes", {"quoteIdentifiers": "always"}) == "codmes"   # proyecciones: solo si hace falta
    assert render.proj_ident("select") == "`select`"


def test_type_text_y_create_table_sql():
    assert render.type_text("VARCHAR(128)") == "varchar(128)"
    assert render.type_text("STRING", {"typeCase": "upper"}) == "STRING"
    assert render.type_text("ARRAY<STRUCT<a:STRING>>") == "ARRAY<STRUCT<a:STRING>>"     # complejos tal cual
    assert render.create_table_sql({}) == "CREATE OR REPLACE TABLE"
    assert render.create_table_sql({"createTable": "if-not-exists", "external": True}) == \
        "CREATE EXTERNAL TABLE IF NOT EXISTS"
    assert render.create_table_sql({"createTable": "if-not-exists"}) == "CREATE TABLE IF NOT EXISTS"


def test_tags_sobre_vistas_alter_table_por_default_y_alter_view_por_setting():
    rule = {"id": "t", "name": "t", "kind": "rule", "target": "table", "condition": "",
            "action": {"tags": {"k": "v"}}, "appliesTo": ["ddl.vista_negocio"], "priority": 1,
            "enabled": True, "validationState": "valid"}
    ctx = {"tabla": {"udp": {}}, "modelo": {"udp": {}}}
    assert render.table_tag_statements([rule], "ddl.vista_negocio", ctx, {}, "s.v", object_kind="view")[0] == \
        ["ALTER TABLE s.v SET TAGS ('k' = 'v');"]
    assert render.table_tag_statements([rule], "ddl.vista_negocio", ctx, {}, "s.v", object_kind="view",
                                       options={"viewTagsAs": "view"})[0] == ["ALTER VIEW s.v SET TAGS ('k' = 'v');"]
    col_rule = {**rule, "target": "column"}
    cols = {"c": {"nombre": "c", "udp": {}}}
    assert render.column_tag_statements([col_rule], "ddl.vista_negocio", ctx, cols, {}, "s.v",
                                        {"viewTagsAs": "view"}, object_kind="view")[0] == \
        ["ALTER VIEW s.v ALTER COLUMN c SET TAGS ('k' = 'v');"]
    # sobre una TABLA, siempre ALTER TABLE
    assert render.column_tag_statements([col_rule], "ddl.vista_negocio", ctx, cols, {}, "s.t",
                                        {"viewTagsAs": "view"}, object_kind="table")[0] == \
        ["ALTER TABLE s.t ALTER COLUMN c SET TAGS ('k' = 'v');"]


def test_tabla_generada_con_nombres_que_necesitan_comillas_y_keys():
    """Review Focus #1: los nombres raros conservan backticks y el DDL compila."""
    table = {"physicalName": "HD BASE", "schema": "bcp_ddv", "udpValues": {}}
    cols = [{"physicalName": "COD CLI", "dataType": "CHAR(10)", "ddlType": "char(10)", "ordinal": 0,
             "isPrimaryKey": True, "isNullable": False, "udpValues": {}},
            {"physicalName": "select", "dataType": "INTEGER", "ddlType": "int", "ordinal": 1, "udpValues": {}}]
    base = {"tabla": table_ctx(table, {}), "modelo": {"nombre": "", "udp": {}}, "columnas": []}
    ctx = {c["physicalName"]: column_ctx(c, {}) for c in cols}
    stmts, _ = g.run_generators([REJ], table, cols, base, ctx, {}, {"identifierCase": "lower", "includeKeys": True})
    assert stmts[0]["sql"] == ("CREATE OR REPLACE TABLE bcp_ddv.`hd base_rej` (\n"
                               "  `cod cli` char(10) NOT NULL,\n"
                               "  `select` int\n"
                               ")\nUSING DELTA;")
    sqlglot.parse_one(stmts[0]["sql"].rstrip(";"), read="databricks")
    # sin keys no hay NOT NULL (espejo del front)
    stmts2, _ = g.run_generators([REJ], table, cols, base, ctx, {}, {"identifierCase": "lower"})
    assert "NOT NULL" not in stmts2[0]["sql"]


def test_vista_con_columna_palabra_reservada_se_decora_y_sigue_citada():
    rule = {"id": "d", "name": "dec", "kind": "rule", "target": "column",
            "condition": 'columna.udp["Clasificacion del Dato"] LIKE \'DAC-%\'',
            "action": {"expression": "upper({col})", "alias": "{columna.nombre}"},
            "appliesTo": ["ddl.vista_negocio"], "priority": 1, "enabled": True, "validationState": "valid"}
    defs = [{"id": "u", "name": "Clasificacion del Dato", "level": "column"}]
    cols = {"select": column_ctx({"physicalName": "select", "dataType": "STRING",
                                  "udpValues": {"u": "DAC-NOMBRE"}}, names_by_id(defs))}
    sql = "CREATE OR REPLACE VIEW s.v AS\nSELECT\n  `select` AS `select`,\n  x AS x\nFROM s.t;"
    out, log = render.decorate_view_sql(sql, "ddl.vista_negocio", [rule], {}, {"tabla": {"udp": {}}}, cols)
    assert "upper(`select`) AS `select`" in out and any(e["status"] == "applied" for e in log)
    sqlglot.parse_one(out.rstrip(";"), read="databricks")
