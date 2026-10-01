"""Doc 93 · semilla: `updateFrequency`, isDAC de la vista de negocio según las
columnas que proyecta (ANY_COLUMN), particiones desde el UDP, 17 elementos."""
from __future__ import annotations

from app.features.ddl_rules.engine import pipeline
from app.features.ddl_rules.templates import SEED_LOOKUPS, SEED_RULES, TEMPLATES

BY = {r["name"]: r for r in SEED_RULES}


def test_conteo_y_nombres():
    assert len([r for r in SEED_RULES if r["kind"] == "rule"]) == 12
    assert len([r for r in SEED_RULES if r["kind"] == "generator"]) == 5
    assert {"tags_isdac_vista_negocio", "tags_isdac_vista_negocio_sin_dac"} <= set(BY)
    assert len(TEMPLATES) == 10 and any(t["id"] == "tag-by-columns" for t in TEMPLATES)


def test_update_frequency_bien_escrito():
    assert BY["tags_update_frequency"]["action"] == {"tags": {"updateFrequency": "{lookup:update_frequency_map}"}}
    assert not any("Frecuency" in str(r) for r in SEED_RULES)


def test_isdac_por_tipo_de_objeto():
    assert "ddl.vista_negocio" not in BY["tags_isdac"]["appliesTo"]
    assert BY["tags_isdac"]["appliesTo"] == ["ddl.tabla_fisica", "ddl.tabla_rej",
                                             "ddl.vista_tecnica_dac", "ddl.vista_rej_dac"]
    assert BY["tags_isdac_vista_negocio"]["condition"].startswith("ANY_COLUMN(")
    assert BY["tags_isdac_vista_negocio_sin_dac"]["condition"].startswith("NOT ANY_COLUMN(")
    assert BY["tags_isdac_vista_negocio"]["appliesTo"] == ["ddl.vista_negocio"]


def test_layout_declara_partition_udp():
    assert BY["particiones_en_partitioned_by"]["action"] == {
        "layout": {"partitionColumns": "partitioned-by", "partitionUdp": "Particion"}}    # doc 107


DEFS = [
    {"id": "u-dac-col", "name": "Clasificacion del Dato", "level": "column", "dataType": "list",
     "allowedValues": ["No DAC", "No Definido", "DAC-NOMBRE"]},
    {"id": "u-dac-tab", "name": "Clasificacion del Dato", "level": "table", "dataType": "list",
     "allowedValues": ["No DAC", "No Definido", "DAC"]},
    {"id": "u-vac", "name": "Frecuencia Vacuum", "level": "table", "dataType": "list",
     "allowedValues": list(SEED_LOOKUPS["vacuum_map"]["values"])},
    {"id": "u-part", "name": "Particion", "level": "column", "dataType": "list",
     "allowedValues": ["No Definido", "PART_01"]},
]
_ID = {(d["level"], d["name"]): d["id"] for d in DEFS}
CONFIG = {"lookups": {n: {**{k: v for k, v in lk.items() if k != "fromUdpName"},
                          "fromUdpId": _ID[(lk["fromLevel"], lk["fromUdpName"])]}
                      for n, lk in SEED_LOOKUPS.items()}, "functions": []}
RULES = [{**r, "id": f"s{i}", "validationState": "valid"} for i, r in enumerate(SEED_RULES)]


def test_vu_sin_columnas_dac_sale_isdac_false_aunque_la_tabla_sea_dac():
    """El bug H3 del doc 93: antes la `_vu` sin columnas DAC salía isDAC=True."""
    table = {"id": "t1", "physicalName": "T", "schema": "s",
             "udpValues": {"u-dac-tab": "DAC", "u-vac": "MONTHLY_90 days"}}
    cols = [{"physicalName": "A", "dataType": "STRING", "ordinal": 0, "udpValues": {"u-dac-col": "No DAC"}},
            {"physicalName": "B", "dataType": "STRING", "ordinal": 1, "udpValues": {"u-dac-col": "DAC-NOMBRE"}}]
    views = [{"name": "t_dac", "schema": "s_vu", "sourceTableIds": ["t1"],
              "sql": "CREATE OR REPLACE VIEW s_vu.t_dac AS\nSELECT\n  a AS a,\n  b AS b\nFROM s.t;"},
             {"name": "t", "schema": "s_vu", "sourceTableIds": ["t1"],
              "sql": "CREATE OR REPLACE VIEW s_vu.t AS\nSELECT\n  a AS a\nFROM s.t;"}]
    payload = {"model": {"name": "M", "udpValues": {}}, "options": {"identifierCase": "lower"},
               "tables": [{"table": table, "columns": cols, "baseSql": "CREATE TABLE s.t (a string, b string);"}],
               "views": views}
    out = pipeline.render_export(payload, RULES, CONFIG, DEFS, {})

    def tags(name):
        return [s["sql"] for s in out["statements"]
                if s["artifact"] == "ddl.vista_negocio.tags" and s["name"] == name]

    assert any("'isDAC' = 'True'" in t for t in tags("t_dac"))
    assert any("'isDAC' = 'False'" in t for t in tags("t")) and not any("'True'" in t for t in tags("t"))
    assert any("'updateFrequency' = 'MONTHLY'" in t for t in tags("t"))
