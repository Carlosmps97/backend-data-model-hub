"""Generadores (doc 30 F4): emit + toposort + cascada + composición con reglas
de columna (la prueba de fuego del spec §8.7)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
import sqlglot
from fastapi import HTTPException

from app.features.ddl_rules.engine import generators as g
from app.features.ddl_rules.engine.context import column_ctx, names_by_id, table_ctx

DEFS = [
    {"id": "u-dac-col", "name": "Clasificacion del Dato", "level": "column"},
    {"id": "u-tv", "name": "Tipo de Vista", "level": "table"},
]
N = names_by_id(DEFS)

TABLE = {"physicalName": "tbl_cliente", "schema": "core",
         "udpValues": {"u-tv": "Regular"}}
COLS = [
    {"physicalName": "cod_cliente", "dataType": "STRING", "ordinal": 0,
     "isNullable": False, "isPrimaryKey": True, "udpValues": {}},
    {"physicalName": "nom_cliente", "dataType": "STRING", "ordinal": 1,
     "udpValues": {"u-dac-col": "DAC-NOMBRE"}},
    {"physicalName": "fec_alta", "dataType": "DATE", "ordinal": 2, "udpValues": {}},
]
BASE = {"tabla": table_ctx(TABLE, N), "modelo": {"nombre": "DDV", "udp": {}}}
COLS_CTX = {c["physicalName"]: column_ctx(c, N) for c in COLS}

TABLA_REJ = {
    "id": "g1", "name": "tabla_rechazos", "kind": "generator",
    "sourceArtifact": "ddl.tabla_fisica", "condition": "tabla.tipo = 'physical'",
    "action": {"emit": {
        "artifact": "ddl.tabla_rej", "type": "table",
        "schema": "{tabla.esquema}", "name": "{tabla.nombre}_rej",
        "columns": {"inherit": "all", "force_type": "STRING",
                    "strip": ["not_null", "pk", "fk", "default", "check"]},
        "add_columns": [{"name": "rej_motivo", "type": "STRING"},
                        {"name": "rej_fecha", "type": "TIMESTAMP"}],
    }},
    "priority": 200, "enabled": True, "validationState": "valid",
}
VISTA_REJ = {
    "id": "g2", "name": "vista_rechazos", "kind": "generator",
    "sourceArtifact": "ddl.tabla_rej", "condition": "true",
    "action": {"emit": {"artifact": "ddl.vista_rej", "type": "view",
                        "schema": "{tabla.esquema}_v", "name": "{tabla.nombre}_rej",
                        "columns": {"inherit": "all"}}},
    "priority": 190, "enabled": True, "validationState": "valid",
}
VISTA_TEC = {
    "id": "g3", "name": "vista_tecnica", "kind": "generator",
    "sourceArtifact": "ddl.tabla_fisica",
    "condition": 'tabla.udp["Tipo de Vista"] = \'Regular\'',
    "action": {"emit": {"artifact": "ddl.vista_tecnica", "type": "view",
                        "schema": "{tabla.esquema}_v", "name": "{tabla.nombre}",
                        "columns": {"inherit": "all"}}},
    "priority": 180, "enabled": True, "validationState": "valid",
}
ENMASCARAR = {
    "id": "r1", "name": "enmascarar_dac", "kind": "rule", "target": "column",
    "condition": 'columna.udp["Clasificacion del Dato"] LIKE \'DAC-%\'',
    "action": {"expression": "sha2({col}, 512)", "alias": "{columna.nombre}"},
    "appliesTo": ["ddl.vista_tecnica"], "priority": 100, "enabled": True,
    "validationState": "valid",
}


def test_toposort_resuelve_la_cascada():
    ordered, unreachable = g.generator_order([VISTA_REJ, VISTA_TEC, TABLA_REJ])
    assert [x["name"] for x in ordered] == ["tabla_rechazos", "vista_tecnica", "vista_rechazos"]
    assert unreachable == []


def test_ciclo_se_detecta_al_validar_no_en_runtime():
    a = {**TABLA_REJ, "id": "ga", "name": "gen_a", "sourceArtifact": "ddl.x",
         "action": {"emit": {"artifact": "ddl.y", "type": "table"}}}
    b = {**TABLA_REJ, "id": "gb", "name": "gen_b", "sourceArtifact": "ddl.y",
         "action": {"emit": {"artifact": "ddl.x", "type": "table"}}}
    err = g.cycle_error([a, b])
    assert err and "cycle" in err and "gen_a" in err and "gen_b" in err
    # huérfano sin ciclo (su padre aún no existe) NO es error acá
    assert g.cycle_error([VISTA_REJ]) is None


def test_golden_tabla_rechazos_spec_8_5():
    """all STRING, sin PK/NOT NULL (strip), + columnas rej_*; DATE sale STRING.
    Formato ESPEJO del export del canvas: backticks + `USING delta`."""
    stmts, log = g.run_generators([TABLA_REJ], TABLE, COLS, BASE, COLS_CTX, {})
    assert len(stmts) == 1
    sql = stmts[0]["sql"]
    assert sql == (
        "CREATE TABLE IF NOT EXISTS `core`.`tbl_cliente_rej` (\n"
        "  `cod_cliente`  STRING,\n"
        "  `nom_cliente`  STRING,\n"
        "  `fec_alta`     STRING,\n"
        "  `rej_motivo`   STRING,\n"
        "  `rej_fecha`    TIMESTAMP\n"
        ")\nUSING delta;"
    )
    assert "NOT NULL" not in sql                       # strip[not_null, pk]
    assert stmts[0]["artifact"] == "ddl.tabla_rej"


def test_rej_espejo_del_fisico_casing_location_particiones():
    """Pedido owner 07-20: el _rej es IGUAL que el físico — identifier casing,
    LOCATION con carpeta `<nombre>_rej`, mismas particiones y USING — solo que
    el nombre termina en _rej y los tipos van en STRING."""
    cols = [*COLS[:2], {"physicalName": "fec_alta", "dataType": "DATE", "ordinal": 2,
                        "isPartition": True, "udpValues": {}}]
    opts = {"identifierCase": "upper", "tableFormat": "delta", "external": True,
            "location": "s3://dl/warehouse", "includePartitions": True}
    stmts, _ = g.run_generators([TABLA_REJ], TABLE, cols, BASE,
                                COLS_CTX, {}, opts)
    sql = stmts[0]["sql"]
    assert "CREATE EXTERNAL TABLE IF NOT EXISTS `CORE`.`TBL_CLIENTE_REJ`" in sql
    assert "`COD_CLIENTE`" in sql and "STRING" in sql          # casing + force_type
    assert "PARTITIONED BY (`FEC_ALTA`)" in sql                # partición heredada
    assert "LOCATION 's3://dl/warehouse/tbl_cliente_rej'" in sql  # carpeta cruda + _rej
    # Orden (pedido owner): heredadas → añadidas → columnas de PARTICIÓN al final.
    order = [sql.index(f"`{n}`") for n in ("COD_CLIENTE", "REJ_MOTIVO", "FEC_ALTA")]
    assert order == sorted(order)
    # sin includePartitions no se emite la cláusula (igual que la física)
    stmts2, _ = g.run_generators([TABLA_REJ], TABLE, cols, BASE, COLS_CTX, {},
                                 {**opts, "includePartitions": False})
    assert "PARTITIONED BY" not in stmts2[0]["sql"]


def test_cascada_completa_fisica_rej_vista_rej():
    """spec §13: la cascada tabla_fisica → tabla_rej → vista_rej se resuelve
    completa; la vista hereda las columnas DERIVADAS (rej_* incluidas)."""
    stmts, _ = g.run_generators([VISTA_REJ, TABLA_REJ], TABLE, COLS, BASE, COLS_CTX, {})
    assert [s["artifact"] for s in stmts] == ["ddl.tabla_rej", "ddl.vista_rej"]
    view = stmts[1]["sql"]
    assert "CREATE OR REPLACE VIEW `core_v`.`tbl_cliente_rej` AS" in view
    assert "rej_motivo" in view and "FROM `core`.`tbl_cliente_rej`;" in view
    sqlglot.parse_one(view.rstrip(";"), read="databricks")


def test_composicion_generador_mas_regla_spec_8_7():
    """La prueba de fuego: vista_tecnica crea el artefacto y enmascarar_dac lo
    decora SOLO porque lo tiene en appliesTo — no se conocen entre sí."""
    stmts, log = g.run_generators([VISTA_TEC, ENMASCARAR], TABLE, COLS, BASE, COLS_CTX, {})
    view = stmts[0]["sql"]
    assert "CREATE OR REPLACE VIEW `core_v`.`tbl_cliente` AS" in view
    assert "sha2(nom_cliente, 512) AS nom_cliente" in view
    assert "cod_cliente" in view and "sha2(cod_cliente" not in view
    assert any(e.get("rule") == "enmascarar_dac" and e["status"] == "applied" for e in log)


def test_la_rej_no_hereda_el_sha2():
    """spec §7.4: herencia explícita — enmascarar_dac NO tiene ddl.vista_rej en
    appliesTo, así que la cascada de rechazos sale SIN hash."""
    stmts, _ = g.run_generators([VISTA_REJ, TABLA_REJ, ENMASCARAR],
                                TABLE, COLS, BASE, COLS_CTX, {})
    assert all("sha2" not in s["sql"] for s in stmts)


def test_condicion_false_no_emite():
    tabla_personalizada = {**TABLE, "udpValues": {"u-tv": "Personalizada"}}
    base = {"tabla": table_ctx(tabla_personalizada, N), "modelo": {"nombre": "", "udp": {}}}
    stmts, _ = g.run_generators([VISTA_TEC], tabla_personalizada, COLS, base, COLS_CTX, {})
    assert stmts == []


def test_generador_disabled_corta_su_cascada_sin_abortar():
    off = {**TABLA_REJ, "enabled": False}
    stmts, log = g.run_generators([VISTA_REJ, off], TABLE, COLS, BASE, COLS_CTX, {})
    assert stmts == []
    reasons = {e.get("reason") for e in log if e["status"] == "skipped"}
    assert "disabled" in reasons
    assert any("unresolved source" in (r or "") for r in reasons)   # vista_rej quedó sin fuente


# ── Guards del apply (cascada) ─────────────────────────────────────────────

def _mock_apply(monkeypatch, before_rules):
    from app.features.data_standards import service as s
    monkeypatch.setattr(s.dom_repo, "list_domains", AsyncMock(return_value=[]))
    monkeypatch.setattr(s.dict_repo, "list_entries", AsyncMock(return_value=[]))
    monkeypatch.setattr(s.udp_repo, "list_udp", AsyncMock(return_value=DEFS))
    monkeypatch.setattr(s.rules_repo, "list_rules", AsyncMock(return_value=before_rules))
    monkeypatch.setattr(s.rules_repo, "get_config",
                        AsyncMock(return_value={"id": "global", "lookups": {}, "functions": []}))
    for fn in ("create_rule", "update_rule", "delete_rule", "set_config"):
        monkeypatch.setattr(s.rules_repo, fn, AsyncMock())
    monkeypatch.setattr(s, "current_snapshot", AsyncMock(return_value={}))
    monkeypatch.setattr(s.repository, "insert_version_next_seq",
                        AsyncMock(side_effect=lambda f: {**f, "seq": 7, "label": "v7", "id": "v7"}))
    monkeypatch.setattr(s, "audit", AsyncMock())
    return s


def test_apply_borrar_generador_fuente_409(monkeypatch):
    s = _mock_apply(monkeypatch, [dict(TABLA_REJ), dict(VISTA_REJ)])
    from app.features.data_standards.schemas import ApplyBody
    with pytest.raises(HTTPException) as e:
        asyncio.run(s.apply("mr", ApplyBody(rulesDelete=["g1"])))
    assert e.value.status_code == 409 and "vista_rechazos" in e.value.detail
    # borrar la cascada COMPLETA sí procede
    v = asyncio.run(s.apply("mr", ApplyBody(rulesDelete=["g1", "g2"])))
    assert v["seq"] == 7


def test_apply_ciclo_en_el_batch_400(monkeypatch):
    s = _mock_apply(monkeypatch, [])
    from app.features.data_standards.schemas import ApplyBody, DdlRuleEdit
    a = DdlRuleEdit(name="gen_a", kind="generator", sourceArtifact="ddl.y",
                    condition="true", action={"emit": {"artifact": "ddl.x", "type": "table"}})
    b = DdlRuleEdit(name="gen_b", kind="generator", sourceArtifact="ddl.x",
                    condition="true", action={"emit": {"artifact": "ddl.y", "type": "table"}})
    with pytest.raises(HTTPException) as e:
        asyncio.run(s.apply("mr", ApplyBody(rulesUpsert=[a, b])))
    assert e.value.status_code == 400 and "cycle" in e.value.detail
