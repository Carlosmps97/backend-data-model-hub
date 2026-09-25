"""Doc 90 · Data types: `action.types = {CHAR: VARCHAR}` (regla de COLUMNA)
renombra el tipo BASE de las columnas que matchean al exportar — en el CREATE
físico del front (reescritura por TOKEN: comentarios, literales e
identificadores intactos, el resto byte-idéntico) y en las tablas generadas
(la `_rej` conserva el tipo de sus particiones → también mapean). Los
argumentos `(n)`/`(p,s)` se conservan si el tipo destino los admite (gramática
del catálogo, `ARG_SPECS`); si no, se descartan (`CHAR(10)` → `STRING`).
Semilla `char_a_varchar` + plantilla del picker + bench (Test/Impact)."""
from __future__ import annotations

import asyncio
import re
from unittest.mock import AsyncMock

from app.features.ddl_rules.engine import generators as g
from app.features.ddl_rules.engine import pipeline, render
from app.features.ddl_rules.engine import validate as v
from app.features.ddl_rules.engine.context import column_ctx, names_by_id, table_ctx
from app.features.ddl_rules.templates import SEED_RULES, TABLE_ARTIFACTS, TEMPLATES

CHAR_VARCHAR = {"id": "ty1", "name": "char_a_varchar", "kind": "rule", "target": "column",
                "condition": "", "action": {"types": {"CHAR": "VARCHAR"}},
                "appliesTo": ["ddl.tabla_fisica", "ddl.tabla_rej"], "priority": 80,
                "enabled": True, "validationState": "valid"}
REJ = {"id": "g1", "name": "tabla_rechazos", "kind": "generator", "sourceArtifact": "ddl.tabla_fisica",
       "condition": "", "priority": 200, "enabled": True, "validationState": "valid",
       "action": {"emit": {"artifact": "ddl.tabla_rej", "type": "table", "schema": "{tabla.esquema}",
                           "name": "{tabla.nombre}_rej",
                           "columns": {"inherit": "all", "force_type": "STRING", "keep_partition_type": True,
                                       "strip": ["not_null", "pk"]},
                           "add_columns": [{"name": "tiporeject", "type": "STRING"}]}}}
DEFS = [{"id": "u-dac-col", "name": "Clasificacion del Dato", "level": "column", "dataType": "list",
         "allowedValues": ["No DAC", "No Definido", "DAC-NOMBRE"]}]
N = names_by_id(DEFS)
TABLE = {"id": "t1", "physicalName": "HD_VENTA", "schema": "BCP_DDV", "udpValues": {}}
COLS = [
    {"physicalName": "CODCLAVECIC", "dataType": "CHAR(10)", "ordinal": 0, "isPrimaryKey": True,
     "isNullable": False, "udpValues": {"u-dac-col": "DAC-NOMBRE"}},
    {"physicalName": "NOMCLIENTE", "dataType": "VARCHAR(50)", "ordinal": 1, "udpValues": {}},
    {"physicalName": "CHAR", "dataType": "CHAR(3)", "ordinal": 2, "udpValues": {}},   # columna LLAMADA char
    {"physicalName": "MTOSALDO", "dataType": "DECIMAL(18,2)", "ordinal": 3, "udpValues": {}},
    {"physicalName": "CODMES", "dataType": "CHAR(6)", "ordinal": 4, "isPartition": True, "udpValues": {}},
]
BASE = {"tabla": table_ctx(TABLE, N), "modelo": {"nombre": "DDV", "udp": {}}}
CTX = {c["physicalName"]: column_ctx(c, N) for c in COLS}
CONFIG = {"lookups": {}, "functions": []}
# El CREATE físico lo emite el FRONT (casing lower, tipos en MAYÚSCULA verbatim,
# COMMENT por columna cuando el export lo pide).
BASE_SQL = ("CREATE EXTERNAL TABLE IF NOT EXISTS `bcp_ddv`.`hd_venta` (\n"
            "  `codclavecic` CHAR(10) NOT NULL COMMENT 'Código CHAR(10) del cliente',\n"
            "  `nomcliente` VARCHAR(50),\n"
            "  `char` CHAR(3),\n"
            "  `mtosaldo` DECIMAL(18,2),\n"
            "  `codmes` CHAR(6) NOT NULL,\n"
            "  CONSTRAINT `pk_hd_venta` PRIMARY KEY (`codclavecic`)\n"
            ")\nUSING delta\nPARTITIONED BY (`codmes`)\n"
            "LOCATION 'abfss://x/HD_VENTA';")
EXPECTED_SQL = (BASE_SQL
                .replace("`codclavecic` CHAR(10)", "`codclavecic` VARCHAR(10)")
                .replace("`char` CHAR(3)", "`char` VARCHAR(3)")
                .replace("`codmes` CHAR(6)", "`codmes` VARCHAR(6)"))
PAYLOAD = {"model": {"name": "DDV", "udpValues": {}}, "options": {"identifierCase": "lower"},
           "tables": [{"table": TABLE, "columns": COLS, "baseSql": BASE_SQL}], "views": []}
ARTS = ["ddl.tabla_fisica", "ddl.vista_negocio", "ddl.tabla_rej", "ddl.vista_tecnica"]
KINDS = {"ddl.tabla_fisica": "table", "ddl.vista_negocio": "view", "ddl.tabla_rej": "table",
         "ddl.vista_tecnica": "view"}

M = [("r", {"CHAR": "VARCHAR"})]


def _txt(text, maps=M):
    return render.map_type_text(text, maps)[0]


# ── map_type_text (puro, sobre un tipo suelto) ────────────────────────────


def test_map_type_text_renombra_la_base_y_conserva_los_argumentos():
    assert _txt("CHAR(10)") == "VARCHAR(10)"
    assert _txt("CHAR") == "VARCHAR"                        # sin argumentos: solo la base
    assert _txt("CHAR (12)") == "VARCHAR (12)"              # el texto alrededor no se toca
    assert _txt("VARCHAR(50)") == "VARCHAR(50)"             # VARCHAR no es CHAR (match exacto, no substring)
    assert _txt("NCHAR(5)") == "NCHAR(5)"
    assert _txt("DECIMAL(18,2)") == "DECIMAL(18,2)"


def test_map_type_text_respeta_el_casing_del_original_y_es_case_insensitive():
    assert _txt("char(8)") == "varchar(8)"
    assert _txt("Char(8)") == "VARCHAR(8)"                  # mixto → como lo escribió la regla
    assert _txt("CHAR(8)", [("r", {"char": "varchar"})]) == "VARCHAR(8)"


def test_map_type_text_entra_en_tipos_complejos():
    assert _txt("STRUCT<a: CHAR(3), b: ARRAY<char(2)>>") == "STRUCT<a: VARCHAR(3), b: ARRAY<varchar(2)>>"
    assert _txt("MAP<STRING, INT>") == "MAP<STRING, INT>"


def test_map_type_text_descarta_los_argumentos_si_el_destino_no_los_admite():
    assert _txt("CHAR(10)", [("r", {"CHAR": "STRING"})]) == "STRING"
    assert _txt("DECIMAL(18,2)", [("r", {"DECIMAL": "DOUBLE"})]) == "DOUBLE"
    assert _txt("CHAR(10)", [("r", {"CHAR": "DECIMAL"})]) == "DECIMAL(10)"   # el destino sí admite


def test_map_type_text_encadena_las_reglas_en_orden():
    chain = [("a", {"CHAR": "VARCHAR"}), ("b", {"VARCHAR": "STRING"})]
    text, applied = render.map_type_text("CHAR(10)", chain)
    assert text == "STRING" and applied == ["a", "b"]
    assert render.map_type_text("VARCHAR(5)", chain) == ("STRING", ["b"])
    assert render.map_type_text("INT", chain) == ("INT", [])


# ── CREATE físico del front (reescritura por token) ────────────────────────


def test_apply_column_types_sql_reescribe_solo_los_tipos_del_create():
    out, log = render.apply_column_types_sql(BASE_SQL, [CHAR_VARCHAR], "ddl.tabla_fisica", BASE, CTX, CONFIG)
    assert out == EXPECTED_SQL
    # el COMMENT con 'CHAR(10)' adentro, la columna LLAMADA `char`, el
    # CONSTRAINT y el PARTITIONED BY quedaron intactos
    assert "COMMENT 'Código CHAR(10) del cliente'" in out and "`char` VARCHAR(3)" in out
    assert "PARTITIONED BY (`codmes`)" in out and "PRIMARY KEY (`codclavecic`)" in out
    applied = [(e["column"], e["object"]) for e in log if e["status"] == "applied"]
    assert applied == [("codclavecic", "CHAR(10) → VARCHAR(10)"), ("char", "CHAR(3) → VARCHAR(3)"),
                       ("codmes", "CHAR(6) → VARCHAR(6)")]
    assert all(e["rule"] == "char_a_varchar" and e["artifact"] == "ddl.tabla_fisica" for e in log)


def test_apply_column_types_sql_no_toca_un_tipo_complejo_anidado_y_mapea_el_char_vecino():
    # Doc 92 D6: el tokenizador separa `>>>` en tres GT; el ARRAY<STRUCT<…>> queda
    # intacto y el CHAR de la columna vecina se mapea a VARCHAR.
    sql = ("CREATE TABLE s.t (\n"
           "  `payload` ARRAY<STRUCT<codcampania:VARCHAR(30),v:MAP<VARCHAR(30),DECIMAL(11,2)>>>,\n"
           "  `codigo` CHAR(3)\n)")
    ctx = {**CTX, "payload": {"udp": {}, "column": {"name": "payload"}}, "codigo": {"udp": {}, "column": {"name": "codigo"}}}
    out, _log = render.apply_column_types_sql(sql, [CHAR_VARCHAR], "ddl.tabla_fisica", BASE, ctx, CONFIG)
    assert "ARRAY<STRUCT<codcampania:VARCHAR(30),v:MAP<VARCHAR(30),DECIMAL(11,2)>>>" in out
    assert "`codigo` VARCHAR(3)" in out


def test_apply_column_types_sql_respeta_la_condicion_por_columna():
    solo_dac = {**CHAR_VARCHAR, "condition": 'columna.udp["Clasificacion del Dato"] LIKE \'DAC-%\''}
    out, log = render.apply_column_types_sql(BASE_SQL, [solo_dac], "ddl.tabla_fisica", BASE, CTX, CONFIG)
    assert out == BASE_SQL.replace("`codclavecic` CHAR(10)", "`codclavecic` VARCHAR(10)")
    assert [e["column"] for e in log] == ["codclavecic"]


def test_apply_column_types_sql_sin_reglas_o_sin_matches_es_byte_identico():
    assert render.apply_column_types_sql(BASE_SQL, [], "ddl.tabla_fisica", BASE, CTX, CONFIG) == (BASE_SQL, [])
    otra = {**CHAR_VARCHAR, "action": {"types": {"NCHAR": "STRING"}}}
    assert render.apply_column_types_sql(BASE_SQL, [otra], "ddl.tabla_fisica", BASE, CTX, CONFIG) == (BASE_SQL, [])
    # artefacto fuera de appliesTo → nada
    assert render.apply_column_types_sql(BASE_SQL, [CHAR_VARCHAR], "ddl.vista_tecnica", BASE, CTX, CONFIG) == (BASE_SQL, [])
    # disabled / invalid se saltan por `runnable` en el pipeline; acá la regla llega ya filtrada


def test_apply_column_types_sql_multi_statement_solo_toca_la_lista_de_columnas():
    base = BASE_SQL + ("\nALTER TABLE `bcp_ddv`.`hd_venta` ADD CONSTRAINT `fk_x` FOREIGN KEY (`char`)"
                       " REFERENCES `bcp_ddv`.`otra` (`char`);")
    out, _ = render.apply_column_types_sql(base, [CHAR_VARCHAR], "ddl.tabla_fisica", BASE, CTX, CONFIG)
    assert out == EXPECTED_SQL + base[len(BASE_SQL):]


def test_apply_column_types_sql_con_nombres_sin_comillas_que_son_palabras_clave():
    """Doc 93 D13: sin comillas, una columna llamada `char` o `array` se
    tokeniza como palabra clave — igual es la cabeza de su definición."""
    sql = ("CREATE OR REPLACE TABLE bcp_ddv.hd_venta (\n  codclavecic char(10),\n  char char(3),\n"
           "  array string\n)\nUSING DELTA;")
    ctx = {**CTX, "ARRAY": {**CTX["CHAR"], "nombre": "ARRAY", "tipo": "STRING"}}
    out, log = render.apply_column_types_sql(sql, [CHAR_VARCHAR], "ddl.tabla_fisica", BASE, ctx, CONFIG)
    assert out == (sql.replace("codclavecic char(10)", "codclavecic varchar(10)")
                      .replace("char char(3)", "char varchar(3)"))
    assert [e["column"] for e in log if e["status"] == "applied"] == ["codclavecic", "char"]


def test_apply_column_types_sql_texto_no_tokenizable_queda_intacto():
    raw = "CREATE TABLE t (`a` CHAR(3) COMMENT 'sin cerrar);"
    out, log = render.apply_column_types_sql(raw, [CHAR_VARCHAR], "ddl.tabla_fisica", BASE,
                                             {"a": {**CTX["CHAR"], "nombre": "a"}}, CONFIG)
    assert out == raw and log and log[0]["status"] == "skipped"


# ── Tablas generadas + pipeline ─────────────────────────────────────────────


def test_generador_mapea_los_tipos_que_conserva_la_rej():
    stmts, log = g.run_generators([REJ, CHAR_VARCHAR], TABLE, COLS, BASE, CTX, CONFIG, {"identifierCase": "lower"})
    sql = stmts[0]["sql"]
    assert re.search(r"\n  codmes varchar\(6\)\n", sql)          # partición: conserva el tipo → mapeado
    assert re.search(r"\n  codclavecic string,", sql)            # force_type manda en las demás
    assert re.search(r"\n  tiporeject string,", sql)
    assert [e for e in log if e.get("rule") == "char_a_varchar"] == [
        {"rule": "char_a_varchar", "column": "CODMES", "status": "applied", "artifact": "ddl.tabla_rej",
         "object": "CHAR(6) → VARCHAR(6)"}]
    # sin la regla sobre la _rej (appliesTo solo física) la partición sale tal cual
    solo_fisica = {**CHAR_VARCHAR, "appliesTo": ["ddl.tabla_fisica"]}
    stmts2, _ = g.run_generators([REJ, solo_fisica], TABLE, COLS, BASE, CTX, CONFIG, {"identifierCase": "lower"})
    assert re.search(r"\n  codmes char\(6\)\n", stmts2[0]["sql"])


def test_pipeline_fisica_y_rej_e_idempotencia():
    out = pipeline.render_export(PAYLOAD, [CHAR_VARCHAR, REJ], CONFIG, DEFS, {})
    by_art = {s["artifact"]: s["sql"] for s in out["statements"]}
    assert by_art["ddl.tabla_fisica"] == EXPECTED_SQL
    rej = by_art["ddl.tabla_rej"]
    assert "varchar(6)" in rej and "char(6)" not in rej.replace("varchar", "")
    applied = [(e["artifact"], e["column"]) for e in out["log"] if e.get("rule") == "char_a_varchar"]
    assert applied == [("ddl.tabla_fisica", "codclavecic"), ("ddl.tabla_fisica", "char"),
                       ("ddl.tabla_fisica", "codmes"), ("ddl.tabla_rej", "CODMES")]
    # idempotente (§7.6): volver a pasar la salida por el motor no cambia nada
    again = pipeline.render_export({**PAYLOAD, "tables": [{**PAYLOAD["tables"][0], "baseSql": EXPECTED_SQL}]},
                                   [CHAR_VARCHAR, REJ], CONFIG, DEFS, {})
    assert {s["artifact"]: s["sql"] for s in again["statements"]} == by_art
    # deshabilitada → skipped y CREATE intacto
    off = pipeline.render_export(PAYLOAD, [{**CHAR_VARCHAR, "enabled": False}, REJ], CONFIG, DEFS, {})
    assert next(s for s in off["statements"] if s["artifact"] == "ddl.tabla_fisica")["sql"] == BASE_SQL
    assert [e["status"] for e in off["log"] if e.get("rule") == "char_a_varchar"] == ["skipped"]


# ── Validación ──────────────────────────────────────────────────────────────


def test_validate_types_ok_y_errores_estructurales():
    ok = v.validate_rule(CHAR_VARCHAR, DEFS, CONFIG, ARTS, KINDS)
    assert ok["state"] == "valid" and ok["errors"] == [] and ok["warnings"] == []

    def msgs(rule):
        rep = v.validate_rule(rule, DEFS, CONFIG, ARTS, KINDS)
        return rep["state"], " | ".join(e["message"] for e in rep["errors"]), rep["warnings"]

    assert msgs({**CHAR_VARCHAR, "action": {"types": {}}})[0] == "invalid"
    assert msgs({**CHAR_VARCHAR, "action": {"types": "CHAR"}})[0] == "invalid"
    state, m, _ = msgs({**CHAR_VARCHAR, "action": {"types": {"CHAR(10)": "VARCHAR"}}})
    assert state == "invalid" and "without arguments" in m
    state, m, _ = msgs({**CHAR_VARCHAR, "action": {"types": {"CHAR": "VARCHAR(10)"}}})
    assert state == "invalid" and "without arguments" in m
    state, m, _ = msgs({**CHAR_VARCHAR, "target": "table"})
    assert state == "invalid" and "column rule" in m
    state, m, _ = msgs({**CHAR_VARCHAR, "action": {"types": {"CHAR": "VARCHAR"}, "expression": "trim({col})"}})
    assert state == "invalid" and "can't be combined" in m
    # sobre un artefacto VISTA valida pero avisa (las vistas no declaran tipos)
    state, _, warns = msgs({**CHAR_VARCHAR, "appliesTo": ["ddl.vista_tecnica"]})
    assert state == "valid" and any("data type mappings only apply to table artifacts" in w for w in warns)


def test_semilla_char_a_varchar_y_plantilla():
    seed = next(r for r in SEED_RULES if r["name"] == "char_a_varchar")
    assert seed["kind"] == "rule" and seed["target"] == "column" and seed["condition"] == ""
    assert seed["action"] == {"types": {"CHAR": "VARCHAR"}}
    assert seed["appliesTo"] == list(TABLE_ARTIFACTS)          # física + _rej (las vistas no tipan)
    assert v.validate_rule({**seed, "id": None, "udpRefs": []}, DEFS, CONFIG, ARTS, KINDS)["state"] == "valid"
    tpl = next(t for t in TEMPLATES if t["id"] == "map-types")
    assert tpl["rule"] is seed


# ── Bench del editor: Test contra una tabla real + Impact ──────────────────


def _mock_service(monkeypatch):
    from app.features.ddl_rules import service as svc
    monkeypatch.setattr(svc.udp_repo, "list_udp", AsyncMock(return_value=DEFS))
    monkeypatch.setattr(svc.repository, "get_config",
                        AsyncMock(return_value={"id": "global", "lookups": {}, "functions": []}))
    monkeypatch.setattr(svc.repository, "list_rules", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc.dom_repo, "list_domains", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc.catalog_repo, "list_tables_by_ids", AsyncMock(return_value=[TABLE]))
    monkeypatch.setattr(svc.catalog_repo, "list_columns", AsyncMock(return_value=COLS))
    return svc


def test_service_test_rule_types_solo_lista_las_columnas_que_cambian(monkeypatch):
    svc = _mock_service(monkeypatch)
    out = asyncio.run(svc.test_rule("p1", CHAR_VARCHAR, "t1"))
    assert out["matched"] == 3 and out["total"] == 5
    frags = {f["column"]: f for f in out["fragments"]}
    assert set(frags) == {"CODCLAVECIC", "CHAR", "CODMES"}
    assert frags["CODCLAVECIC"]["sql"] == "codclavecic varchar(10)"
    assert frags["CODCLAVECIC"]["why"] == "CHAR(10) → VARCHAR(10)"


def test_service_impact_types_cuenta_solo_columnas_que_cambian(monkeypatch):
    svc = _mock_service(monkeypatch)
    monkeypatch.setattr(svc.repository, "tables_light",
                        AsyncMock(return_value=[{**TABLE}, {"id": "t2", "physicalName": "tbl_x",
                                                            "schema": "core", "udpValues": {}}]))
    cols = [{**c, "tableId": "t1", "id": f"c{i}"} for i, c in enumerate(COLS)]
    cols.append({"id": "c9", "tableId": "t2", "physicalName": "X", "dataType": "STRING", "ordinal": 0, "udpValues": {}})
    monkeypatch.setattr(svc.repository, "columns_light", AsyncMock(return_value=cols))
    assert asyncio.run(svc.impact("p1", CHAR_VARCHAR)) == {"columns": 3, "tables": 1}
