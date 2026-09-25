"""Fase 5 (doc 30): tags por columna (UNA sentencia por columna, spec §8.2),
tags de tabla (multi-par, §8.3) y TBLPROPERTIES con lookup dentro del CREATE
base (§8.4)."""
from __future__ import annotations

from app.features.ddl_rules.engine import render
from app.features.ddl_rules.engine.context import column_ctx, names_by_id, table_ctx

DEFS = [
    {"id": "u-dac-col", "name": "Clasificacion del Dato", "level": "column"},
    {"id": "u-dom", "name": "Dominio Principal", "level": "table"},
    {"id": "u-ec", "name": "Estado Cloud", "level": "table"},
    {"id": "u-te", "name": "Tipo de Entidad", "level": "table"},
    {"id": "u-vac", "name": "Frecuencia Vacuum", "level": "table"},
]
N = names_by_id(DEFS)
TABLE = {"physicalName": "tbl_cliente", "schema": "core",
         "udpValues": {"u-dom": "CLIENTES", "u-ec": "Migracion", "u-te": "Referencia",
                       "u-vac": "CUSTOM_90 days"}}
COLS = [
    {"physicalName": "cod_cliente", "dataType": "STRING", "ordinal": 0, "udpValues": {}},
    {"physicalName": "nom_cliente", "dataType": "STRING", "ordinal": 1,
     "udpValues": {"u-dac-col": "DAC-NOMBRE"}},
    {"physicalName": "num_documento", "dataType": "STRING", "ordinal": 2,
     "udpValues": {"u-dac-col": "DAC-DOCUMENTO"}},
]
BASE = {"tabla": table_ctx(TABLE, N), "modelo": {"nombre": "", "udp": {}}}
COLS_CTX = {c["physicalName"]: column_ctx(c, N) for c in COLS}
FULL = "core.tbl_cliente"          # doc 71 H2 / doc 93 D2: calificado como el CREATE (sin comillas)

TAGS_COL = {
    "id": "r1", "name": "tags_clasificacion", "kind": "rule", "target": "column",
    "condition": 'columna.udp["Clasificacion del Dato"] <> \'No Definido\'',
    "action": {"tags": {"clasificacion_dato": "{udp:Clasificacion del Dato}"}},
    "appliesTo": ["ddl.tabla_fisica"], "priority": 50, "enabled": True,
    "validationState": "valid",
}
TAGS_TAB = {
    "id": "r2", "name": "tags_tabla", "kind": "rule", "target": "table",
    "condition": 'tabla.udp["Dominio Principal"] IS NOT NULL',
    "action": {"tags": {"dominio_principal": "{udp:Dominio Principal}",
                        "estado_cloud": "{udp:Estado Cloud}",
                        "tipo_entidad": "{udp:Tipo de Entidad}"}},
    "appliesTo": ["ddl.tabla_fisica"], "priority": 50, "enabled": True,
    "validationState": "valid",
}
VACUUM = {
    "id": "r3", "name": "tblproperties_vacuum", "kind": "rule", "target": "table",
    "condition": 'tabla.udp["Frecuencia Vacuum"] IS NOT NULL',
    "action": {"tblproperties": {"delta.deletedFileRetentionDuration": "{lookup:vacuum_map}"}},
    "appliesTo": ["ddl.tabla_fisica"], "priority": 40, "enabled": True,
    "validationState": "valid",
}
CONFIG = {"lookups": {"vacuum_map": {
    "fromUdpId": "u-vac", "fromName": "Frecuencia Vacuum", "fromLevel": "table",
    "values": {"CUSTOM_90 days": "interval 90 days", "DAILY_15 days": "interval 15 days"},
    "default": None}}}

CREATE_BASE = ("CREATE TABLE IF NOT EXISTS core.tbl_cliente (\n"
               "  cod_cliente STRING,\n  nom_cliente STRING,\n  num_documento STRING\n"
               ")\nUSING delta;")


def test_golden_column_tags_una_sentencia_por_columna():
    """Restricción Databricks (spec §8.2): imposible taggear 2 columnas en un
    ALTER — una sentencia por columna, en el orden de las columnas."""
    stmts, log = render.column_tag_statements([TAGS_COL], "ddl.tabla_fisica",
                                              BASE, COLS_CTX, {}, FULL)
    assert stmts == [
        "ALTER TABLE core.tbl_cliente ALTER COLUMN nom_cliente SET TAGS ('clasificacion_dato' = 'DAC-NOMBRE');",
        "ALTER TABLE core.tbl_cliente ALTER COLUMN num_documento SET TAGS ('clasificacion_dato' = 'DAC-DOCUMENTO');",
    ]
    assert sum(1 for e in log if e["status"] == "applied") == 2   # cod_cliente sin UDP → fuera


def test_golden_table_tags_multipar_ordenado():
    stmts, _ = render.table_tag_statements([TAGS_TAB], "ddl.tabla_fisica", BASE, {}, FULL)
    assert stmts == ["ALTER TABLE core.tbl_cliente SET TAGS ("
                     "'dominio_principal' = 'CLIENTES', "
                     "'estado_cloud' = 'Migracion', "
                     "'tipo_entidad' = 'Referencia');"]


def test_golden_tblproperties_vacuum_con_lookup():
    out, log = render.apply_tblproperties(CREATE_BASE, [VACUUM], "ddl.tabla_fisica",
                                          BASE, CONFIG)
    assert "TBLPROPERTIES (" in out
    assert "'delta.deletedFileRetentionDuration'='interval 90 days'" in out.replace(" = ", "=")
    assert any(e["status"] == "applied" for e in log)


def test_tblproperties_lookup_null_no_emite_nada():
    """spec §6.7: valor sin mapear + default null → no se emite; CREATE intacto."""
    tabla_rara = {**TABLE, "udpValues": {**TABLE["udpValues"], "u-vac": "RARA_999"}}
    base = {"tabla": table_ctx(tabla_rara, N), "modelo": {"nombre": "", "udp": {}}}
    out, _ = render.apply_tblproperties(CREATE_BASE, [VACUUM], "ddl.tabla_fisica",
                                        base, CONFIG)
    assert out == CREATE_BASE


def test_tblproperties_vacuum_default_del_lookup_para_tabla_sin_valor():
    """Enfoque B (decisión owner 07-21): la tabla SIN 'Frecuencia Vacuum'
    asignado (como las 163 del DDV real, usedBy=0) + condición ABIERTA + el
    campo `default` del lookup → emite el default del lookup. El motor sigue
    leyendo solo lo explícito (no inyecta el default del def); el 'sin valor →
    default' se declara EN LA REGLA. Ver 30b-HALLAZGOS-UDP-DEFAULTS.md."""
    sin_vac = {**TABLE, "udpValues": {k: v for k, v in TABLE["udpValues"].items()
                                      if k != "u-vac"}}
    base = {"tabla": table_ctx(sin_vac, N), "modelo": {"nombre": "", "udp": {}}}
    assert "Frecuencia Vacuum" not in base["tabla"]["udp"]      # el motor NO rellena
    rule = {**VACUUM, "condition": ""}                          # aplica siempre
    config = {"lookups": {"vacuum_map": {**CONFIG["lookups"]["vacuum_map"],
                                         "default": "interval 90 days"}}}
    out, log = render.apply_tblproperties(CREATE_BASE, [rule], "ddl.tabla_fisica",
                                          base, config)
    assert "'delta.deletedFileRetentionDuration'='interval 90 days'" in out.replace(" = ", "=")
    assert any(e["status"] == "applied" for e in log)


def test_tblproperties_no_pisa_key_del_base():
    base_con_prop = CREATE_BASE.replace(
        "USING delta;", "USING delta\nTBLPROPERTIES ('delta.deletedFileRetentionDuration' = 'interval 7 days');")
    out, log = render.apply_tblproperties(base_con_prop, [VACUUM], "ddl.tabla_fisica",
                                          BASE, CONFIG)
    assert out == base_con_prop                       # lo del modeler manda
    assert any("already set" in (e.get("reason") or "") for e in log)


def test_tag_valor_largo_se_salta_con_log():
    larga = {**TAGS_TAB, "action": {"tags": {"nota": "x" * 300}}}
    stmts, log = render.table_tag_statements([larga], "ddl.tabla_fisica", BASE, {}, FULL)
    assert stmts == []
    assert any("256" in (e.get("reason") or "") for e in log)


def test_tags_respetan_condicion_y_artefacto():
    stmts, _ = render.column_tag_statements([TAGS_COL], "ddl.vista_tecnica",
                                            BASE, COLS_CTX, {}, FULL)
    assert stmts == []                                # appliesTo manda (spec §7.4)
    sin_dom = {**TABLE, "udpValues": {}}
    base2 = {"tabla": table_ctx(sin_dom, N), "modelo": {"nombre": "", "udp": {}}}
    stmts2, _ = render.table_tag_statements([TAGS_TAB], "ddl.tabla_fisica", base2, {}, FULL)
    assert stmts2 == []                               # IS NOT NULL falso


# ── Doc 71 H1 · inyección TEXTUAL de TBLPROPERTIES ────────────────────────

FRONT_CREATE = ("CREATE TABLE IF NOT EXISTS `core`.`tbl_cliente` (\n"
                "  `cod_cliente` STRING NOT NULL COMMENT 'Codigo del cliente',\n"
                "  `fec_alta` DATE\n"
                ")\nUSING delta\nPARTITIONED BY (`fec_alta`)\nCOMMENT 'Maestro; de clientes'\n"
                "LOCATION 's3://dl/warehouse/tbl_cliente';\n"
                "ALTER TABLE `core`.`tbl_cliente` ADD CONSTRAINT `fk_x` FOREIGN KEY (`cod_cliente`) "
                "REFERENCES `core`.`p`(`id`);\n")


def test_tblproperties_inyeccion_textual_conserva_el_formato_del_front():
    """El CREATE del generador del front (comentarios con `;` adentro, particiones,
    LOCATION, ALTER FK detrás) queda byte-idéntico salvo el bloque nuevo."""
    out, log = render.apply_tblproperties(FRONT_CREATE, [VACUUM], "ddl.tabla_fisica", BASE, CONFIG)
    expected = FRONT_CREATE.replace(
        "LOCATION 's3://dl/warehouse/tbl_cliente';",
        "LOCATION 's3://dl/warehouse/tbl_cliente'\nTBLPROPERTIES (\n"
        "  'delta.deletedFileRetentionDuration' = 'interval 90 days'\n);")
    assert out == expected
    assert any(e["status"] == "applied" for e in log)


def test_tblproperties_extiende_bloque_existente_en_linea_y_multilinea():
    one_line = CREATE_BASE.replace("USING delta;", "USING delta\nTBLPROPERTIES ('a' = 'b');")
    out, _ = render.apply_tblproperties(one_line, [VACUUM], "ddl.tabla_fisica", BASE, CONFIG)
    assert out.endswith("TBLPROPERTIES ('a' = 'b', 'delta.deletedFileRetentionDuration' = 'interval 90 days');")
    multi = CREATE_BASE.replace("USING delta;", "USING delta\nTBLPROPERTIES (\n  'a' = 'b'\n);")
    out2, _ = render.apply_tblproperties(multi, [VACUUM], "ddl.tabla_fisica", BASE, CONFIG)
    assert out2.endswith("TBLPROPERTIES (\n  'a' = 'b',\n  'delta.deletedFileRetentionDuration' = 'interval 90 days'\n);")


def test_tblproperties_helpers_de_texto():
    assert render.existing_tblproperty_keys("CREATE TABLE t (a INT) TBLPROPERTIES ('x' = '1', 'y;z' = 'k);v');") == {"x", "y;z"}
    assert render.inject_tblproperties_text("CREATE TABLE t (a INT)", [("k", "v")]) is None   # sin `;` → fallback
    assert render.inject_tblproperties_text("CREATE TABLE t (a INT);", []) == "CREATE TABLE t (a INT);"


def test_tags_de_columna_respetan_casing_del_export():
    stmts, _ = render.column_tag_statements([TAGS_COL], "ddl.tabla_fisica", BASE, COLS_CTX, {},
                                            render.full_name("core", "tbl_cliente", {"identifierCase": "upper"}),
                                            {"identifierCase": "upper"})
    assert stmts[0].startswith("ALTER TABLE CORE.TBL_CLIENTE ALTER COLUMN NOM_CLIENTE SET TAGS (")
