"""Motor de reglas de COLUMNA (doc 30 F3): encadenamiento con AST, orden
determinista, decorado del SELECT y semántica de skip (spec §7.1/7.2/7.5)."""
from __future__ import annotations

import sqlglot

from app.features.ddl_rules.engine import render
from app.features.ddl_rules.engine.expressions import build_expression, expand_template
from app.features.ddl_rules.engine.context import column_ctx, names_by_id, table_ctx

DEFS = [
    {"id": "u-dac-col", "name": "Clasificacion del Dato", "level": "column"},
    {"id": "u-vac", "name": "Frecuencia Vacuum", "level": "table"},
]
N_BY_ID = names_by_id(DEFS)

TABLE = {"physicalName": "tbl_cliente", "schema": "core", "udpValues": {}}
COLS = [
    {"physicalName": "cod_cliente", "dataType": "STRING", "ordinal": 0, "udpValues": {}},
    {"physicalName": "nom_cliente", "dataType": "STRING", "ordinal": 1,
     "udpValues": {"u-dac-col": "DAC-NOMBRE"}},
    {"physicalName": "num_documento", "dataType": "STRING", "ordinal": 2,
     "udpValues": {"u-dac-col": "DAC-DOCUMENTO"}},
    {"physicalName": "fec_alta", "dataType": "DATE", "ordinal": 3, "udpValues": {}},
]
BASE_CTX = {"tabla": table_ctx(TABLE, N_BY_ID), "modelo": {"nombre": "DDV", "udp": {}}}
COLS_CTX = {c["physicalName"]: column_ctx(c, N_BY_ID) for c in COLS}

ENMASCARAR = {
    "id": "r1", "name": "enmascarar_dac", "kind": "rule", "target": "column",
    "condition": 'columna.udp["Clasificacion del Dato"] LIKE \'DAC-%\'',
    "action": {"expression": "sha2({col}, 512)", "alias": "{columna.nombre}"},
    "appliesTo": ["ddl.vista_tecnica", "ddl.vista_negocio"],
    "priority": 100, "enabled": True, "validationState": "valid",
}

VIEW_SQL = ("CREATE OR REPLACE VIEW core_v.tbl_cliente AS\n"
            "SELECT\n  cod_cliente,\n  nom_cliente,\n  num_documento,\n  fec_alta\n"
            "FROM core.tbl_cliente;")


def test_golden_enmascarar_dac_spec_8_1():
    out, log = render.decorate_view_sql(VIEW_SQL, "ddl.vista_tecnica", [ENMASCARAR],
                                        {}, BASE_CTX, COLS_CTX)
    assert out == (
        "CREATE OR REPLACE VIEW core_v.tbl_cliente AS\n"
        "SELECT\n"
        "  cod_cliente,\n"
        "  sha2(nom_cliente, 512) AS nom_cliente,\n"
        "  sha2(num_documento, 512) AS num_documento,\n"
        "  fec_alta\n"
        "FROM core.tbl_cliente;"
    )
    applied = [(e["rule"], e["column"]) for e in log if e["status"] == "applied"]
    assert applied == [("enmascarar_dac", "nom_cliente"), ("enmascarar_dac", "num_documento")]


def test_no_dac_no_dispara():
    """La trampa (spec §3.3-b): 'No DAC' NO debe salir hasheada."""
    cols = {**COLS_CTX, "nom_cliente": column_ctx(
        {**COLS[1], "udpValues": {"u-dac-col": "No DAC"}}, N_BY_ID)}
    out, _ = render.decorate_view_sql(VIEW_SQL, "ddl.vista_tecnica", [ENMASCARAR],
                                      {}, BASE_CTX, cols)
    assert "sha2(nom_cliente" not in out and "sha2(num_documento, 512)" in out


def test_sin_matches_devuelve_sql_byte_identical():
    otra = {**ENMASCARAR, "condition": 'columna.udp["Clasificacion del Dato"] = \'NUNCA\''}
    out, log = render.decorate_view_sql(VIEW_SQL, "ddl.vista_tecnica", [otra],
                                        {}, BASE_CTX, COLS_CTX)
    assert out == VIEW_SQL
    assert not any(e["status"] == "applied" for e in log)


def test_artefacto_fuera_de_applies_to_no_aplica():
    out, _ = render.decorate_view_sql(VIEW_SQL, "ddl.tabla_rej", [ENMASCARAR],
                                      {}, BASE_CTX, COLS_CTX)
    assert out == VIEW_SQL   # herencia SIEMPRE explícita (spec §7.4)


def test_encadenamiento_apila_por_prioridad_y_parsea():
    """spec §7.2: {col} de la regla N es la salida de la N-1 (trim → sha2)."""
    trim = {**ENMASCARAR, "id": "r2", "name": "trim_dac", "priority": 200,
            "action": {"expression": "trim({col})"}}
    out, log = render.decorate_view_sql(VIEW_SQL, "ddl.vista_tecnica",
                                        [ENMASCARAR, trim], {}, BASE_CTX, COLS_CTX)
    assert "sha2(trim(nom_cliente), 512) AS nom_cliente" in out
    sqlglot.parse_one(out, read="databricks")            # el resultado parsea
    ordered = [e["rule"] for e in log if e["column"] == "nom_cliente"]
    assert ordered == ["trim_dac", "enmascarar_dac"]      # 200 antes que 100


def test_orden_desempata_por_nombre():
    a = {**ENMASCARAR, "id": "ra", "name": "a_regla", "priority": 100,
         "action": {"expression": "upper({col})"}}
    ordered = render.run_order([ENMASCARAR, a])
    assert [r["name"] for r in ordered] == ["a_regla", "enmascarar_dac"]


def test_disabled_e_invalid_se_saltan_sin_abortar():
    """spec §7.5: no abortan el export; van al log de la corrida."""
    off = {**ENMASCARAR, "id": "r3", "name": "apagada", "enabled": False}
    rota = {**ENMASCARAR, "id": "r4", "name": "rota", "validationState": "invalid"}
    out, log = render.decorate_view_sql(VIEW_SQL, "ddl.vista_tecnica",
                                        [off, rota, ENMASCARAR], {}, BASE_CTX, COLS_CTX)
    assert "sha2(nom_cliente, 512)" in out               # la sana aplicó
    skipped = {(e["rule"], e["reason"]) for e in log if e["status"] == "skipped"}
    assert ("apagada", "disabled") in skipped and ("rota", "invalid") in skipped


def test_render_error_en_una_regla_no_frena_las_demas():
    mala = {**ENMASCARAR, "id": "r5", "name": "zz_mala", "priority": 100,
            "action": {"expression": "sha2({col}, "}}   # SQL roto en runtime
    out, log = render.decorate_view_sql(VIEW_SQL, "ddl.vista_tecnica",
                                        [mala, ENMASCARAR], {}, BASE_CTX, COLS_CTX)
    assert "sha2(nom_cliente, 512)" in out
    assert any(e["rule"] == "zz_mala" and e["status"] == "skipped" for e in log)


def test_funcion_reusable_en_expresion():
    """spec §6.8: la custom function expande a CASE con el UDP interpolado."""
    fns = [{"name": "enmascarar", "params": ["col", "nivel"], "body":
            "CASE WHEN '{nivel}' LIKE 'DAC-TARJETA%' THEN concat('****', right({col}, 4)) "
            "WHEN '{nivel}' LIKE 'DAC-%' THEN sha2({col}, 512) ELSE {col} END"}]
    regla = {**ENMASCARAR, "action": {
        "expression": '{enmascarar(col, columna.udp["Clasificacion del Dato"])}'}}
    out, _ = render.decorate_view_sql(VIEW_SQL, "ddl.vista_tecnica", [regla],
                                      {"functions": fns}, BASE_CTX, COLS_CTX)
    assert "WHEN 'DAC-NOMBRE' LIKE 'DAC-TARJETA%'" in out   # pretty parte el CASE en líneas
    assert "sha2(nom_cliente, 512)" in out
    sqlglot.parse_one(out, read="databricks")


def test_build_expression_col_directo_y_alias():
    """{col} puede ser TODA la expresión (identidad) sin romper."""
    col = sqlglot.parse_one("nom_cliente", read="databricks")
    ast = build_expression("{col}", {"columna": COLS_CTX["nom_cliente"]}, col)
    assert ast.sql(dialect="databricks") == "nom_cliente"


def test_expand_template_lookup_null_no_emite():
    """spec §6.7: valor sin mapear con default null → None (no se emite nada)."""
    lookups = {"vacuum_map": {"fromName": "Frecuencia Vacuum", "fromLevel": "table",
                              "values": {"CUSTOM_90 days": "interval 90 days"},
                              "default": None}}
    ctx = {"tabla": {"udp": {"Frecuencia Vacuum": "RARA_999"}}}
    assert expand_template("{lookup:vacuum_map}", ctx, lookups) is None
    ctx_ok = {"tabla": {"udp": {"Frecuencia Vacuum": "CUSTOM_90 days"}}}
    assert expand_template("{lookup:vacuum_map}", ctx_ok, lookups) == "interval 90 days"


def test_resolve_lookup_names_enriquece_from_name():
    lk = render.resolve_lookup_names({"m": {"fromUdpId": "u-vac", "values": {}}}, N_BY_ID)
    assert lk["m"]["fromName"] == "Frecuencia Vacuum"
