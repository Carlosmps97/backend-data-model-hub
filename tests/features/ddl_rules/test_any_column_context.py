"""Doc 93 D8 · `ANY_COLUMN(...)` ve las columnas que el objeto REALMENTE tiene:
física → todas; tabla generada → las suyas; vista → lo que proyecta tras las
exclusiones; vista de negocio ilegible → desconocido (la regla se salta con
motivo en el log, nunca un tag equivocado)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.ddl_rules.engine import pipeline

DEFS = [
    {"id": "u-dac-col", "name": "Clasificacion del Dato", "level": "column", "dataType": "list",
     "allowedValues": ["No DAC", "No Definido", "DAC-CUENTA"]},
    {"id": "u-dac-tab", "name": "Clasificacion del Dato", "level": "table", "dataType": "list",
     "allowedValues": ["No DAC", "No Definido", "DAC"]},
]
DAC_ANY = 'ANY_COLUMN(columna.udp["Clasificacion del Dato"] LIKE \'DAC-%\')'


def _rule(rid: str, *, target="table", condition="", action, applies, priority=50, kind="rule", **extra):
    return {"id": rid, "name": rid, "kind": kind, "target": target, "condition": condition,
            "action": action, "appliesTo": applies, "priority": priority, "enabled": True,
            "validationState": "valid", **extra}


FREQ = _rule("freq", action={"tags": {"freq": "M"}}, priority=60,
             applies=["ddl.tabla_fisica", "ddl.vista_tecnica", "ddl.vista_negocio"])
PHYS_HAS_DAC = _rule("phys_has_dac", condition=DAC_ANY, action={"tags": {"hasDac": "yes"}},
                     applies=["ddl.tabla_fisica"])
VU_TRUE = _rule("vu_true", condition=DAC_ANY, action={"tags": {"isDAC": "True"}}, applies=["ddl.vista_negocio"])
VU_FALSE = _rule("vu_false", condition=f"NOT {DAC_ANY}", action={"tags": {"isDAC": "False"}},
                 applies=["ddl.vista_negocio"])
TEC_TRUE = _rule("tec_true", condition=DAC_ANY, action={"tags": {"isDAC": "True"}}, applies=["ddl.vista_tecnica"])
TEC_FALSE = _rule("tec_false", condition=f"NOT {DAC_ANY}", action={"tags": {"isDAC": "False"}},
                  applies=["ddl.vista_tecnica"])
EXCL = _rule("excl", target="column", condition='columna.udp["Clasificacion del Dato"] LIKE \'DAC-%\'',
             action={"exclude": True}, applies=["ddl.vista_tecnica"], priority=100)
G_TEC = _rule("g_tec", kind="generator", target=None, priority=180, sourceArtifact="ddl.tabla_fisica",
              action={"emit": {"artifact": "ddl.vista_tecnica", "type": "view", "schema": "{tabla.esquema}_v",
                               "name": "{tabla.nombre}", "columns": {"inherit": "all"}}}, applies=[])
G_ONLY_DAC = _rule("g_only_dac", kind="generator", target=None, priority=170, condition=DAC_ANY,
                   sourceArtifact="ddl.tabla_fisica",
                   action={"emit": {"artifact": "ddl.vista_solo_dac", "type": "view", "schema": "{tabla.esquema}_d",
                                    "name": "{tabla.nombre}", "columns": {"inherit": "all"}}}, applies=[])
RULES = [FREQ, PHYS_HAS_DAC, VU_TRUE, VU_FALSE, TEC_TRUE, TEC_FALSE, EXCL, G_TEC, G_ONLY_DAC]

TABLE = {"id": "t1", "physicalName": "T", "schema": "s", "udpValues": {"u-dac-tab": "DAC"}}
COLS = [{"physicalName": "A", "dataType": "STRING", "ordinal": 0, "udpValues": {"u-dac-col": "No DAC"}},
        {"physicalName": "B", "dataType": "STRING", "ordinal": 1, "udpValues": {"u-dac-col": "DAC-CUENTA"}}]
CLEAN = {"id": "t2", "physicalName": "U", "schema": "s", "udpValues": {}}
CLEAN_COLS = [{"physicalName": "A", "dataType": "STRING", "ordinal": 0, "udpValues": {"u-dac-col": "No DAC"}}]


def _payload(views=(), tables=None):
    return {"model": {"name": "M", "udpValues": {}}, "options": {"identifierCase": "lower"},
            "tables": tables if tables is not None else [
                {"table": TABLE, "columns": COLS, "baseSql": "CREATE TABLE s.t (a string, b string);"}],
            "views": list(views)}


def _tags(out, artifact, name=None):
    return [s["sql"] for s in out["statements"]
            if s["artifact"] == f"{artifact}.tags" and (name is None or s["name"] == name)]


def test_fisica_ve_todas_sus_columnas():
    out = pipeline.render_export(_payload(), RULES, {}, DEFS, {})
    assert any("'hasDac' = 'yes'" in t for t in _tags(out, "ddl.tabla_fisica"))
    clean = pipeline.render_export(_payload(tables=[{"table": CLEAN, "columns": CLEAN_COLS,
                                                     "baseSql": "CREATE TABLE s.u (a string);"}]), RULES, {}, DEFS, {})
    assert not any("hasDac" in t for t in _tags(clean, "ddl.tabla_fisica"))


def test_vistas_de_negocio_por_lo_que_proyectan():
    vu_dac = {"name": "vu_dac", "schema": "s_vu", "sourceTableIds": ["t1"],
              "sql": "CREATE OR REPLACE VIEW s_vu.vu_dac AS\nSELECT\n  a AS a,\n  b AS b\nFROM s.t;"}
    vu_plain = {"name": "vu_plain", "schema": "s_vu", "sourceTableIds": ["t1"],
                "sql": "CREATE OR REPLACE VIEW s_vu.vu_plain AS\nSELECT\n  a AS a\nFROM s.t;"}
    out = pipeline.render_export(_payload([vu_dac, vu_plain]), RULES, {}, DEFS, {})
    assert any("'isDAC' = 'True'" in t for t in _tags(out, "ddl.vista_negocio", "vu_dac"))
    plain = _tags(out, "ddl.vista_negocio", "vu_plain")
    assert any("'isDAC' = 'False'" in t for t in plain) and not any("'True'" in t for t in plain)


def test_vista_generada_ve_lo_que_quedo_tras_excluir():
    out = pipeline.render_export(_payload(), RULES, {}, DEFS, {})
    tec = _tags(out, "ddl.vista_tecnica")
    assert any("'isDAC' = 'False'" in t for t in tec)            # la columna DAC se excluyó
    assert not any("'isDAC' = 'True'" in t for t in tec)


def test_vista_ilegible_salta_la_regla_con_motivo_y_mantiene_el_resto():
    """Review Focus #2: sin columnas conocidas, ANY_COLUMN no inventa isDAC."""
    broken = {"name": "vu_rota", "schema": "s_vu", "sourceTableIds": ["t1"],
              "sql": "CREATE VIEW s_vu.vu_rota AS SELEC a FRM s.t"}
    out = pipeline.render_export(_payload([broken]), RULES, {}, DEFS, {})
    tags = _tags(out, "ddl.vista_negocio", "vu_rota")
    assert tags and all("isDAC" not in t for t in tags) and any("'freq' = 'M'" in t for t in tags)
    assert any(e["status"] == "skipped" and "ANY_COLUMN" in (e.get("reason") or "") for e in out["log"])


def test_vista_con_select_estrella_no_inventa_isdac():
    """Final review #2: `SELECT *` (o `t.*`) parsea pero NO dice qué columnas
    expone — se trata como columnas desconocidas: ANY_COLUMN se salta con motivo
    (antes salía isDAC='False' sobre una tabla con columnas DAC)."""
    for sql in ("CREATE VIEW s_vu.vu_star AS SELECT * FROM s.t",
                "CREATE VIEW s_vu.vu_star AS SELECT x.* FROM s.t x",
                "CREATE VIEW s_vu.vu_star AS SELECT x.a, x.* FROM s.t x"):
        view = {"name": "vu_star", "schema": "s_vu", "sourceTableIds": ["t1"], "sql": sql}
        out = pipeline.render_export(_payload([view]), RULES, {}, DEFS, {})
        tags = _tags(out, "ddl.vista_negocio", "vu_star")
        assert tags and all("isDAC" not in t for t in tags) and any("'freq' = 'M'" in t for t in tags), sql
        assert any(e["status"] == "skipped" and "ANY_COLUMN" in (e.get("reason") or "") for e in out["log"]), sql


def test_generador_con_any_column_solo_emite_si_la_tabla_tiene_columnas_dac():
    out = pipeline.render_export(_payload(), RULES, {}, DEFS, {})
    assert any(s["artifact"] == "ddl.vista_solo_dac" for s in out["statements"])
    clean = pipeline.render_export(_payload(tables=[{"table": CLEAN, "columns": CLEAN_COLS,
                                                     "baseSql": "CREATE TABLE s.u (a string);"}]), RULES, {}, DEFS, {})
    assert not any(s["artifact"] == "ddl.vista_solo_dac" for s in clean["statements"])


def _mock_service(monkeypatch):
    from app.features.ddl_rules import service as svc
    monkeypatch.setattr(svc.udp_repo, "list_udp", AsyncMock(return_value=DEFS))
    monkeypatch.setattr(svc.repository, "get_config", AsyncMock(return_value={"id": "p1", "lookups": {}, "functions": []}))
    monkeypatch.setattr(svc.repository, "list_rules", AsyncMock(return_value=RULES))
    monkeypatch.setattr(svc.dom_repo, "list_domains", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc.catalog_repo, "list_tables_by_ids", AsyncMock(return_value=[TABLE]))
    monkeypatch.setattr(svc.catalog_repo, "list_columns", AsyncMock(return_value=COLS))
    return svc


def test_bench_e_impact_evaluan_any_column(monkeypatch):
    svc = _mock_service(monkeypatch)
    out = asyncio.run(svc.test_rule("p1", PHYS_HAS_DAC, "t1"))
    assert out["matched"] == 1
    monkeypatch.setattr(svc.repository, "tables_light", AsyncMock(return_value=[TABLE, CLEAN]))
    cols = ([{**c, "tableId": "t1", "id": f"a{i}"} for i, c in enumerate(COLS)]
            + [{**c, "tableId": "t2", "id": f"b{i}"} for i, c in enumerate(CLEAN_COLS)])
    monkeypatch.setattr(svc.repository, "columns_light", AsyncMock(return_value=cols))
    assert asyncio.run(svc.impact("p1", PHYS_HAS_DAC)) == {"columns": 0, "tables": 1}


def test_impact_de_regla_de_columna_con_any_column(monkeypatch):
    """Final review #3: una regla de COLUMNA puede usar ANY_COLUMN (doc 93 D8) y
    el Impact recibe `columnas` (doc 93 §7) — antes respondía 400."""
    svc = _mock_service(monkeypatch)
    monkeypatch.setattr(svc.repository, "tables_light", AsyncMock(return_value=[TABLE, CLEAN]))
    cols = ([{**c, "tableId": "t1", "id": f"a{i}"} for i, c in enumerate(COLS)]
            + [{**c, "tableId": "t2", "id": f"b{i}"} for i, c in enumerate(CLEAN_COLS)])
    monkeypatch.setattr(svc.repository, "columns_light", AsyncMock(return_value=cols))
    rule = _rule("no_dac_en_tabla_dac", target="column",
                 condition=f'{DAC_ANY} AND columna.udp["Clasificacion del Dato"] = \'No DAC\'',
                 action={"tags": {"x": "y"}}, applies=["ddl.tabla_fisica"])
    # solo la columna No DAC de la tabla que SÍ tiene columnas DAC (t1.A)
    assert asyncio.run(svc.impact("p1", rule)) == {"columns": 1, "tables": 1}
