"""Doc 76 · los 7 DDL de la macro BCP salen del ruleset base + las acciones
nuevas del motor: `statements` (before/after), `exclude`, tags sobre vistas
(ALTER VIEW), tags de tabla una sentencia por regla, `keep_partition_type`,
orden de particiones por UDP y LOCATION con carpeta en MAYÚSCULA."""
from __future__ import annotations

from app.features.ddl_rules.engine import generators as g
from app.features.ddl_rules.engine import pipeline, render
from app.features.ddl_rules.engine import validate as v
from app.features.ddl_rules.engine.context import column_ctx, names_by_id, table_ctx
from app.features.ddl_rules.templates import SEED_LOOKUPS, SEED_RULES

DEFS = [
    {"id": "u-dac-col", "name": "Clasificacion del Dato", "level": "column", "dataType": "list",
     "allowedValues": ["No DAC", "No Definido", "DAC-DOCUMENTO", "DAC-NOMBRE", "DAC-CUENTA"]},
    {"id": "u-dac-tab", "name": "Clasificacion del Dato", "level": "table", "dataType": "list",
     "allowedValues": ["No DAC", "No Definido", "DAC"]},
    {"id": "u-vac", "name": "Frecuencia Vacuum", "level": "table", "dataType": "list",
     "allowedValues": list(SEED_LOOKUPS["vacuum_map"]["values"])},
    {"id": "u-part", "name": "Particion", "level": "column", "dataType": "list",
     "allowedValues": ["No Definido", "PART_01", "PART_02", "PART_03"]},
]
_ID = {(d["level"], d["name"]): d["id"] for d in DEFS}
LOOKUPS = {name: {**{k: val for k, val in lk.items() if k != "fromUdpName"},
                  "fromUdpId": _ID[(lk["fromLevel"], lk["fromUdpName"])]}
           for name, lk in SEED_LOOKUPS.items()}
CONFIG = {"lookups": LOOKUPS, "functions": []}
RULES = [{**r, "id": f"seed-{i}", "validationState": "valid"} for i, r in enumerate(SEED_RULES)]

# Tabla DAC con dos particiones: CODMES (PART_02) va PRIMERA en el orden
# físico y FECDIA (PART_01) al final → la macro las emite PART_01, PART_02.
TABLE = {"id": "t1", "physicalName": "HD_VENTA", "schema": "BCP_DDV",
         "udpValues": {"u-dac-tab": "DAC", "u-vac": "DAILY_15 days"}}
COLS = [
    {"physicalName": "CODMES", "dataType": "INT", "ordinal": 0, "isPartition": True,
     "isPrimaryKey": True, "isNullable": False, "udpValues": {"u-part": "PART_02"}},
    {"physicalName": "CODCLAVECIC", "dataType": "STRING", "ordinal": 1, "isNullable": False,
     "isPrimaryKey": True, "udpValues": {"u-dac-col": "No DAC"}},
    {"physicalName": "NOMCLIENTE", "dataType": "STRING", "ordinal": 2,
     "udpValues": {"u-dac-col": "DAC-NOMBRE"}},
    {"physicalName": "MTOSALDO", "dataType": "DECIMAL(18,2)", "ordinal": 3, "udpValues": {}},
    {"physicalName": "FECDIA", "dataType": "DATE", "ordinal": 4, "isPartition": True,
     "udpValues": {"u-part": "PART_01"}},
]
OPTIONS = {"identifierCase": "lower", "tableFormat": "delta", "external": True,
           "location": "abfss://<container_name>@<storage_account_name>.dfs.core.windows.net/<path>",
           "locationFolderCase": "upper", "includePartitions": True, "partitionsLast": True}
# El CREATE físico lo emite el FRONT (ya con casing lower, particiones al final
# ordenadas por el UDP y la carpeta del LOCATION en MAYÚSCULA).
BASE_SQL = ("CREATE EXTERNAL TABLE IF NOT EXISTS `bcp_ddv`.`hd_venta` (\n"
            "  `codclavecic` STRING NOT NULL,\n  `nomcliente` STRING,\n  `mtosaldo` DECIMAL(18,2),\n"
            "  `fecdia` DATE,\n  `codmes` INT NOT NULL\n)\nUSING delta\nPARTITIONED BY (`fecdia`, `codmes`)\n"
            "LOCATION 'abfss://<container_name>@<storage_account_name>.dfs.core.windows.net/<path>/HD_VENTA';")
# Vista de negocio modelada (con alias y una fuente DAC).
VU_SQL = ("CREATE OR REPLACE VIEW `bcp_udv_v`.`venta_vu` AS\nSELECT\n  codclavecic AS codclavecic,\n"
          "  nomcliente AS nombre,\n  mtosaldo AS mtosaldo\nFROM `bcp_ddv`.`hd_venta`;")
PAYLOAD = {"model": {"name": "DDV", "udpValues": {}}, "options": OPTIONS,
           "tables": [{"table": TABLE, "columns": COLS, "baseSql": BASE_SQL}],
           "views": [{"name": "venta_vu", "schema": "bcp_udv_v", "sql": VU_SQL, "sourceTableIds": ["t1"]}]}


def _run(payload=PAYLOAD, rules=RULES):
    return pipeline.render_export(payload, rules, CONFIG, DEFS, {})


def _by_artifact(out):
    return {s["artifact"]: s["sql"] for s in out["statements"] if not s["artifact"].endswith((".tags", ".extra"))}


def _tags(out, artifact):
    return [s["sql"] for s in out["statements"] if s["artifact"] == f"{artifact}.tags"]


def _files(out):
    """Nombres de objeto por artefacto principal (= [TABLE]/[VIEW] del zip)."""
    return {s["artifact"]: s["name"] for s in out["statements"] if not s["artifact"].endswith((".tags", ".extra"))}


def test_tabla_dac_produce_los_7_artefactos_con_sus_nombres():
    out = _run()
    assert _files(out) == {
        "ddl.tabla_fisica": "HD_VENTA", "ddl.tabla_rej": "HD_VENTA_rej",
        "ddl.vista_tecnica": "HD_VENTA", "ddl.vista_tecnica_dac": "HD_VENTAdac",
        "ddl.vista_rej": "HD_VENTA_rej", "ddl.vista_rej_dac": "HD_VENTAdac_rej",
        "ddl.vista_negocio": "venta_vu",
    }


def test_1_tabla_fisica():
    out = _run()
    sql = _by_artifact(out)["ddl.tabla_fisica"]
    assert sql.startswith("-- DROP TABLE IF EXISTS `bcp_ddv`.`hd_venta`;\nCREATE EXTERNAL TABLE")
    assert sql.endswith("LOCATION 'abfss://<container_name>@<storage_account_name>.dfs.core.windows.net/<path>/HD_VENTA'\n"
                        "TBLPROPERTIES (\n  'delta.deletedFileRetentionDuration' = '15 days',\n"
                        "  'delta.logRetentionDuration' = '15 days'\n);")
    assert _tags(out, "ddl.tabla_fisica") == [
        "ALTER TABLE `bcp_ddv`.`hd_venta` SET TAGS ('updateFrecuency' = 'DAILY');",
        "ALTER TABLE `bcp_ddv`.`hd_venta` SET TAGS ('isDAC' = 'True');",
        "ALTER TABLE `bcp_ddv`.`hd_venta` ALTER COLUMN `nomcliente` SET TAGS ('DAC' = 'NOMBRE');",
    ]


def test_2_tabla_rejectados():
    out = _run()
    sql = _by_artifact(out)["ddl.tabla_rej"]
    assert sql == (
        "-- DROP TABLE IF EXISTS `bcp_ddv`.`hd_venta_rej`;\n"
        "CREATE EXTERNAL TABLE IF NOT EXISTS `bcp_ddv`.`hd_venta_rej` (\n"
        "  `codclavecic`  STRING,\n"
        "  `nomcliente`   STRING,\n"
        "  `mtosaldo`     STRING,\n"
        "  `tiporeject`   STRING,\n"
        "  `fecdia`       DATE,\n"
        "  `codmes`       INT\n"
        ")\nUSING delta\nPARTITIONED BY (`fecdia`, `codmes`)\n"
        "LOCATION 'abfss://<container_name>@<storage_account_name>.dfs.core.windows.net/<path>/HD_VENTA_REJ'\n"
        "TBLPROPERTIES (\n  'delta.deletedFileRetentionDuration' = '15 days',\n"
        "  'delta.logRetentionDuration' = '15 days'\n);"
    )
    assert _tags(out, "ddl.tabla_rej") == [
        "ALTER TABLE `bcp_ddv`.`hd_venta_rej` SET TAGS ('updateFrecuency' = 'DAILY');",
        "ALTER TABLE `bcp_ddv`.`hd_venta_rej` SET TAGS ('isDAC' = 'True');",
        "ALTER TABLE `bcp_ddv`.`hd_venta_rej` ALTER COLUMN `nomcliente` SET TAGS ('DAC' = 'NOMBRE');",
    ]


def test_3_vista_tecnica_nodac():
    out = _run()
    sql = _by_artifact(out)["ddl.vista_tecnica"]
    # orden FÍSICO (las particiones no se mueven) y SIN la columna DAC
    assert sql == ("CREATE OR REPLACE VIEW `bcp_ddv_v`.`hd_venta` AS\nSELECT\n  codmes AS codmes,\n"
                   "  codclavecic AS codclavecic,\n  mtosaldo AS mtosaldo,\n  fecdia AS fecdia\n"
                   "FROM `bcp_ddv`.`hd_venta`;")
    assert _tags(out, "ddl.vista_tecnica") == [
        "ALTER VIEW `bcp_ddv_v`.`hd_venta` SET TAGS ('updateFrecuency' = 'DAILY');",
        "ALTER VIEW `bcp_ddv_v`.`hd_venta` SET TAGS ('isDAC' = 'False');",
    ]


def test_4_vista_tecnica_dac():
    out = _run()
    sql = _by_artifact(out)["ddl.vista_tecnica_dac"]
    assert sql == ("CREATE OR REPLACE VIEW `bcp_ddv_v`.`hd_ventadac` AS\nSELECT\n  codmes AS codmes,\n"
                   "  codclavecic AS codclavecic,\n"
                   "  bcp_encrypt_function.decrypt_column_view(nomcliente, 'NOMBRE') AS nomcliente,\n"
                   "  mtosaldo AS mtosaldo,\n  fecdia AS fecdia\nFROM `bcp_ddv`.`hd_venta`;")
    assert _tags(out, "ddl.vista_tecnica_dac") == [
        "ALTER VIEW `bcp_ddv_v`.`hd_ventadac` SET TAGS ('updateFrecuency' = 'DAILY');",
        "ALTER VIEW `bcp_ddv_v`.`hd_ventadac` SET TAGS ('isDAC' = 'True');",
        "ALTER VIEW `bcp_ddv_v`.`hd_ventadac` ALTER COLUMN `nomcliente` SET TAGS ('DAC' = 'NOMBRE');",
    ]


def test_5_y_6_vistas_rejectados():
    out = _run()
    nodac = _by_artifact(out)["ddl.vista_rej"]
    assert nodac == ("CREATE OR REPLACE VIEW `bcp_ddv_v`.`hd_venta_rej` AS\nSELECT\n  codmes AS codmes,\n"
                     "  codclavecic AS codclavecic,\n  mtosaldo AS mtosaldo,\n  fecdia AS fecdia,\n"
                     "  tiporeject AS tiporeject\nFROM `bcp_ddv`.`hd_venta_rej`;")
    dac = _by_artifact(out)["ddl.vista_rej_dac"]
    assert "`bcp_ddv_v`.`hd_ventadac_rej`" in dac
    assert "decrypt_column_view(nomcliente, 'NOMBRE') AS nomcliente" in dac
    assert dac.rstrip(";").endswith("tiporeject AS tiporeject\nFROM `bcp_ddv`.`hd_venta_rej`")
    assert _tags(out, "ddl.vista_rej")[1] == "ALTER VIEW `bcp_ddv_v`.`hd_venta_rej` SET TAGS ('isDAC' = 'False');"
    assert _tags(out, "ddl.vista_rej_dac")[1:] == [
        "ALTER VIEW `bcp_ddv_v`.`hd_ventadac_rej` SET TAGS ('isDAC' = 'True');",
        "ALTER VIEW `bcp_ddv_v`.`hd_ventadac_rej` ALTER COLUMN `nomcliente` SET TAGS ('DAC' = 'NOMBRE');",
    ]


def test_7_vista_negocio_con_alias():
    """La columna DAC de la fuente sale desencriptada conservando el ALIAS de la
    vista, y el tag de columna se emite sobre el nombre de salida."""
    out = _run()
    sql = _by_artifact(out)["ddl.vista_negocio"]
    assert "bcp_encrypt_function.decrypt_column_view(nomcliente, 'NOMBRE') AS nombre" in sql
    assert _tags(out, "ddl.vista_negocio") == [
        "ALTER VIEW `bcp_udv_v`.`venta_vu` SET TAGS ('updateFrecuency' = 'DAILY');",
        "ALTER VIEW `bcp_udv_v`.`venta_vu` SET TAGS ('isDAC' = 'True');",
        "ALTER VIEW `bcp_udv_v`.`venta_vu` ALTER COLUMN `nombre` SET TAGS ('DAC' = 'NOMBRE');",
    ]


def test_tabla_no_dac_sin_artefactos_dac_y_columnas_completas():
    table = {**TABLE, "udpValues": {"u-dac-tab": "No DAC"}}
    out = _run({**PAYLOAD, "tables": [{"table": table, "columns": COLS, "baseSql": BASE_SQL}]})
    arts = _by_artifact(out)
    assert set(arts) == {"ddl.tabla_fisica", "ddl.tabla_rej", "ddl.vista_tecnica", "ddl.vista_rej", "ddl.vista_negocio"}
    assert "nomcliente AS nomcliente" in arts["ddl.vista_tecnica"]      # no se excluye: la tabla no es DAC
    assert _tags(out, "ddl.tabla_fisica")[1] == "ALTER TABLE `bcp_ddv`.`hd_venta` SET TAGS ('isDAC' = 'False');"
    # la columna sigue clasificada DAC-NOMBRE → su tag de columna se mantiene
    assert _tags(out, "ddl.tabla_fisica")[2].endswith("ALTER COLUMN `nomcliente` SET TAGS ('DAC' = 'NOMBRE');")


def test_tabla_sin_udp_cae_a_los_defaults_de_los_lookups():
    """Sin Frecuencia Vacuum ni Clasificacion del Dato (como el DDV real):
    updateFrecuency=CUSTOM, isDAC=False, retención 90 days (enfoque B)."""
    table = {**TABLE, "udpValues": {}}
    out = _run({**PAYLOAD, "tables": [{"table": table, "columns": COLS, "baseSql": BASE_SQL}], "views": []})
    fisica = _by_artifact(out)["ddl.tabla_fisica"]
    assert "'delta.logRetentionDuration' = '90 days'" in fisica
    assert _tags(out, "ddl.tabla_fisica")[:2] == [
        "ALTER TABLE `bcp_ddv`.`hd_venta` SET TAGS ('updateFrecuency' = 'CUSTOM');",
        "ALTER TABLE `bcp_ddv`.`hd_venta` SET TAGS ('isDAC' = 'False');",
    ]


def test_vista_con_todas_las_columnas_excluidas_no_se_emite():
    cols = [{"physicalName": "NOMCLIENTE", "dataType": "STRING", "ordinal": 0, "udpValues": {"u-dac-col": "DAC-NOMBRE"}}]
    out = _run({**PAYLOAD, "tables": [{"table": TABLE, "columns": cols, "baseSql": BASE_SQL}], "views": []})
    arts = _by_artifact(out)
    assert "ddl.vista_tecnica" not in arts and "ddl.vista_tecnica_dac" in arts
    assert not any(s["artifact"] == "ddl.vista_tecnica.tags" for s in out["statements"])   # sin objeto, sin anexos
    # la vista de rechazos conserva `tiporeject` (no es DAC) → sí se emite, solo con esa columna
    assert arts["ddl.vista_rej"].endswith("SELECT\n  tiporeject AS tiporeject\nFROM `bcp_ddv`.`hd_venta_rej`;")
    assert any("every column was excluded" in (e.get("reason") or "") for e in out["log"])


# ── Acciones nuevas del motor, aisladas ───────────────────────────────────

N = names_by_id(DEFS)
# Config como la usa el pipeline (lookups con `fromName` resuelto).
CONFIG_R = {**CONFIG, "lookups": render.resolve_lookup_names(LOOKUPS, N)}
BASE = {"tabla": table_ctx(TABLE, N), "modelo": {"nombre": "", "udp": {}},
        "artefacto": render.artifact_ctx("BCP_DDV", "HD_VENTA", "table", {"identifierCase": "lower"})}
COLS_CTX = {c["physicalName"]: column_ctx(c, N) for c in COLS}


def test_statement_snippets_before_after_y_placeholders():
    rule = {"id": "s1", "name": "extra", "kind": "rule", "target": "table", "condition": "",
            "action": {"statements": {"before": ["-- DROP TABLE IF EXISTS {artefacto.ref};"],
                                      "after": ["OPTIMIZE {artefacto.ref};", "  ", "-- vacuum {lookup:vacuum_map}"]}},
            "appliesTo": ["ddl.tabla_fisica"], "priority": 10, "enabled": True, "validationState": "valid"}
    before, log = render.statement_snippets([rule], "ddl.tabla_fisica", BASE, CONFIG_R, "before")
    assert before == ["-- DROP TABLE IF EXISTS `bcp_ddv`.`hd_venta`;"]
    after, _ = render.statement_snippets([rule], "ddl.tabla_fisica", BASE, CONFIG_R, "after")
    assert after == ["OPTIMIZE `bcp_ddv`.`hd_venta`;", "-- vacuum 15 days"]     # la línea en blanco se omite
    assert any(e["status"] == "applied" and e["object"] == "statements.before" for e in log)
    # otro artefacto → nada
    assert render.statement_snippets([rule], "ddl.tabla_rej", BASE, CONFIG_R, "before")[0] == []
    # en el pipeline los `after` salen como <artefacto>.extra detrás de los tags
    out = _run({**PAYLOAD, "views": []}, [rule])
    assert [s["artifact"] for s in out["statements"]] == ["ddl.tabla_fisica", "ddl.tabla_fisica.extra", "ddl.tabla_fisica.extra"]


def test_table_tags_una_sentencia_por_regla_y_key_repetida_no_se_duplica():
    r1 = {"id": "a", "name": "a_regla", "kind": "rule", "target": "table", "condition": "",
          "action": {"tags": {"isDAC": "True", "owner": "x"}}, "appliesTo": ["ddl.tabla_fisica"],
          "priority": 100, "enabled": True, "validationState": "valid"}
    r2 = {**r1, "id": "b", "name": "b_regla", "priority": 50, "action": {"tags": {"isDAC": "False", "zone": "raw"}}}
    stmts, _ = render.table_tag_statements([r2, r1], "ddl.tabla_fisica", BASE, CONFIG, "`bcp_ddv`.`hd_venta`")
    assert stmts == [
        "ALTER TABLE `bcp_ddv`.`hd_venta` SET TAGS ('isDAC' = 'True', 'owner' = 'x');",
        "ALTER TABLE `bcp_ddv`.`hd_venta` SET TAGS ('zone' = 'raw');",          # isDAC ya lo emitió la de mayor prioridad
    ]
    view_stmts, _ = render.table_tag_statements([r1], "ddl.tabla_fisica", BASE, CONFIG, "`v`.`x`", object_kind="view")
    assert view_stmts[0].startswith("ALTER VIEW `v`.`x` SET TAGS (")


def test_exclude_y_matching_case_insensitive():
    excl = {"id": "e", "name": "excluir", "kind": "rule", "target": "column",
            "condition": 'columna.udp["Clasificacion del Dato"] LIKE \'DAC-%\'',
            "action": {"exclude": True}, "appliesTo": ["ddl.vista_tecnica"],
            "priority": 100, "enabled": True, "validationState": "valid"}
    sql = "CREATE OR REPLACE VIEW v AS SELECT codclavecic AS codclavecic, nomcliente AS nomcliente FROM t;"
    out, log = render.decorate_view_sql(sql, "ddl.vista_tecnica", [excl], CONFIG, BASE, COLS_CTX)
    assert "nomcliente" not in out and "codclavecic AS codclavecic" in out          # matching lower vs físico UPPER
    assert any(e.get("reason") == "excluded" and e["column"] == "nomcliente" for e in log)
    # proyecciones de las que quedan (para los tags de columna de la vista)
    assert list(render.projected_cols_ctx(out, COLS_CTX)) == ["codclavecic"]
    assert render.projected_cols_ctx(out, COLS_CTX)["codclavecic"]["udp"] == {"Clasificacion del Dato": "No DAC"}
    assert render.view_target(out) == "v"


def test_alias_conserva_el_casing_de_la_proyeccion():
    """`{columna.nombre}` expande al físico crudo (UPPER); si solo difiere en
    casing del alias de la proyección (lower), el alias no se pisa."""
    dec = {"id": "d", "name": "dec", "kind": "rule", "target": "column",
           "condition": 'columna.udp["Clasificacion del Dato"] LIKE \'DAC-%\'',
           "action": {"expression": "lower({col})", "alias": "{columna.nombre}"},
           "appliesTo": ["ddl.vista_negocio"], "priority": 100, "enabled": True, "validationState": "valid"}
    sql = "SELECT nomcliente AS nomcliente FROM t"
    out, _ = render.decorate_view_sql(sql, "ddl.vista_negocio", [dec], CONFIG, BASE, COLS_CTX)
    assert " ".join(out.split()) == "SELECT lower(nomcliente) AS nomcliente FROM t"


def test_keep_partition_type_en_emit():
    src = [{"name": "A", "type": "INT", "partition": True}, {"name": "B", "type": "DATE"}]
    cols = g._emit_columns({"columns": {"force_type": "STRING", "keep_partition_type": True},
                            "add_columns": [{"name": "tiporeject", "type": "STRING"}]}, src)
    assert [(c["name"], c["type"], c["added"]) for c in cols] == [("A", "INT", False), ("B", "STRING", False),
                                                                    ("tiporeject", "STRING", True)]
    cols2 = g._emit_columns({"columns": {"force_type": "STRING"}}, src)
    assert [c["type"] for c in cols2] == ["STRING", "STRING"]


ARTS = ["ddl.tabla_fisica", "ddl.vista_negocio", "ddl.vista_tecnica"]
KINDS = {"ddl.tabla_fisica": "table", "ddl.vista_negocio": "view", "ddl.vista_tecnica": "view"}


def test_validate_exclude_y_statements():
    ok = v.validate_rule({"name": "x", "kind": "rule", "target": "column", "condition": "",
                          "action": {"exclude": True}, "appliesTo": ["ddl.vista_tecnica"]}, DEFS, CONFIG, ARTS, KINDS)
    assert ok["state"] == "valid" and ok["warnings"] == []
    bad = v.validate_rule({"name": "x", "kind": "rule", "target": "table", "condition": "",
                           "action": {"exclude": "yes", "expression": "upper({col})"},
                           "appliesTo": ["ddl.vista_tecnica"]}, DEFS, CONFIG, ARTS, KINDS)
    msgs = " | ".join(e["message"] for e in bad["errors"])
    assert "must be `true`" in msgs and "column rule" in msgs and "can't be combined" in msgs
    ok_st = v.validate_rule({"name": "x", "kind": "rule", "target": "table", "condition": "",
                             "action": {"statements": {"before": ["-- DROP TABLE IF EXISTS {artefacto.ref};"]}},
                             "appliesTo": ["ddl.tabla_fisica"]}, DEFS, CONFIG, ARTS, KINDS)
    assert ok_st["state"] == "valid" and ok_st["warnings"] == []
    bad_st = v.validate_rule({"name": "x", "kind": "rule", "target": "column", "condition": "",
                              "action": {"statements": {"middle": "x", "before": [""]}},
                              "appliesTo": ["ddl.tabla_fisica"]}, DEFS, CONFIG, ARTS, KINDS)
    msgs = " | ".join(e["message"] for e in bad_st["errors"])
    assert "Unknown statements position 'middle'" in msgs and "table rule" in msgs and "at least one" in msgs
    unknown = v.validate_rule({"name": "x", "kind": "rule", "target": "table", "condition": "",
                               "action": {"statements": {"before": ["{artefacto.owner}"]}},
                               "appliesTo": ["ddl.tabla_fisica"]}, DEFS, CONFIG, ARTS, KINDS)
    assert any(e["check"] == "Placeholders resolved" for e in unknown["errors"])
