"""Golden end-to-end (doc 30 F6): las 7 reglas semilla del spec §8 corriendo
juntas por el pipeline completo sobre la tabla CLIENTE de ejemplo, +
idempotencia byte-identical (§7.6) + pureza del motor (§13: cero BD)."""
from __future__ import annotations

import asyncio
import importlib
import pathlib
from unittest.mock import AsyncMock

from app.features.ddl_rules.engine import pipeline
from app.features.ddl_rules.templates import SEED_LOOKUPS, SEED_RULES

DEFS = [
    {"id": "u-dac-col", "name": "Clasificacion del Dato", "level": "column", "dataType": "list",
     "allowedValues": ["No DAC", "No Definido", "DAC-DOCUMENTO", "DAC-NOMBRE"]},
    {"id": "u-dac-tab", "name": "Clasificacion del Dato", "level": "table", "dataType": "list",
     "allowedValues": ["No DAC", "No Definido", "DAC"]},
    {"id": "u-dom", "name": "Dominio Principal", "level": "table", "dataType": "string"},
    {"id": "u-ec", "name": "Estado Cloud", "level": "table", "dataType": "list",
     "allowedValues": ["Exclusiva", "Migracion", "No Definido", "No Migrada"]},
    {"id": "u-te", "name": "Tipo de Entidad", "level": "table", "dataType": "list",
     "allowedValues": ["No Definido", "Referencia", "Sub-Tipo", "Super-Tipo"]},
    {"id": "u-vac", "name": "Frecuencia Vacuum", "level": "table", "dataType": "list",
     "allowedValues": list(SEED_LOOKUPS["vacuum_map"]["values"])},
    {"id": "u-tv", "name": "Tipo de Vista", "level": "table", "dataType": "list",
     "allowedValues": ["Regular", "Personalizada"]},
]

# lookups como los persistiría el apply (fromUdpId resuelto).
LOOKUPS = {
    "vacuum_map": {**{k: v for k, v in SEED_LOOKUPS["vacuum_map"].items()
                      if k != "fromUdpName"}, "fromUdpId": "u-vac"},
    "dac_map": {**{k: v for k, v in SEED_LOOKUPS["dac_map"].items()
                   if k != "fromUdpName"}, "fromUdpId": "u-dac-col"},
}
CONFIG = {"lookups": LOOKUPS, "functions": []}

# Las semillas tal cual quedarían guardadas (válidas).
RULES = [{**r, "id": f"seed-{i}", "validationState": "valid"} for i, r in enumerate(SEED_RULES)]

TABLE = {"id": "t1", "physicalName": "tbl_cliente", "schema": "core",
         "udpValues": {"u-dom": "CLIENTES", "u-ec": "Migracion", "u-te": "Referencia",
                       "u-vac": "CUSTOM_90 days", "u-tv": "Regular", "u-dac-tab": "DAC"}}
COLS = [
    {"physicalName": "cod_cliente", "dataType": "STRING", "ordinal": 0,
     "isNullable": False, "isPrimaryKey": True, "udpValues": {}},
    {"physicalName": "nom_cliente", "dataType": "STRING", "ordinal": 1,
     "udpValues": {"u-dac-col": "DAC-NOMBRE"}},
    {"physicalName": "num_documento", "dataType": "STRING", "ordinal": 2,
     "udpValues": {"u-dac-col": "DAC-DOCUMENTO"}},
    {"physicalName": "fec_alta", "dataType": "DATE", "ordinal": 3,
     "udpValues": {"u-dac-col": "No DAC"}},
]
BASE_SQL = ("CREATE TABLE IF NOT EXISTS core.tbl_cliente (\n"
            "  cod_cliente STRING NOT NULL,\n  nom_cliente STRING,\n"
            "  num_documento STRING,\n  fec_alta DATE\n)\nUSING delta;")
VIEW_SQL = ("CREATE OR REPLACE VIEW negocio.clientes AS\n"
            "SELECT\n  cod_cliente,\n  nom_cliente,\n  fec_alta\nFROM core.tbl_cliente;")

PAYLOAD = {
    "model": {"name": "DDV", "udpValues": {}},
    "tables": [{"table": TABLE, "columns": COLS, "baseSql": BASE_SQL}],
    "views": [{"name": "clientes", "schema": "negocio", "sql": VIEW_SQL,
               "sourceTableIds": ["t1"]}],
}


def _run():
    return pipeline.render_export(PAYLOAD, RULES, CONFIG, DEFS, {})


def test_pipeline_produce_todos_los_artefactos_en_orden():
    out = _run()
    arts = [s["artifact"] for s in out["statements"]]
    assert arts == [
        "ddl.tabla_fisica",          # CREATE con TBLPROPERTIES inyectado
        "ddl.tabla_fisica.tags",     # ALTER col nom_cliente
        "ddl.tabla_fisica.tags",     # ALTER col num_documento
        "ddl.tabla_fisica.tags",     # ALTER col fec_alta (No DAC ≠ No Definido → tag)
        "ddl.tabla_fisica.tags",     # ALTER TABLE multi-par
        "ddl.tabla_rej",             # generador 8.5 (prio 200, 1ª pasada)
        "ddl.vista_tecnica",         # generador 8.7 (prio 180, 1ª pasada)
        "ddl.vista_rej",             # cascada 8.6 — espera a su fuente (2ª pasada)
        "ddl.vista_negocio",         # vista del payload decorada (8.1)
    ]


def test_golden_fragmentos_clave():
    out = {s["artifact"]: s for s in _run()["statements"]}
    fisica = out["ddl.tabla_fisica"]["sql"]
    assert "'delta.deletedFileRetentionDuration'='interval 90 days'" in fisica.replace(" = ", "=")
    rej = out["ddl.tabla_rej"]["sql"]
    assert "`core`.`tbl_cliente_rej`" in rej
    assert "DATE" not in rej                      # fec_alta era DATE y sale STRING (8.5)
    assert "rej_archivo" in rej and "NOT NULL" not in rej
    vista_rej = out["ddl.vista_rej"]["sql"]
    assert "`core_v`.`tbl_cliente_rej`" in vista_rej and "sha2" not in vista_rej   # §7.4
    tecnica = out["ddl.vista_tecnica"]["sql"]
    assert "CREATE OR REPLACE VIEW `core_v`.`tbl_cliente` AS" in tecnica
    assert "sha2(nom_cliente, 512) AS nom_cliente" in tecnica                  # §8.7+§8.1
    assert "sha2(fec_alta" not in tecnica                                      # 'No DAC' no dispara
    assert "bcp_ddv_desencrypt" not in tecnica                                 # desencriptar es de negocio
    # Vista de NEGOCIO (pedido owner 07-20): la columna DAC sale con la función
    # de desencriptación y el sufijo del valor UDP (DAC-NOMBRE → 'NOMBRE').
    negocio = out["ddl.vista_negocio"]["sql"]
    assert "bcp_ddv_desencrypt(nom_cliente, 'NOMBRE') AS nom_cliente" in negocio
    assert "sha2" not in negocio                                               # el hash es de la técnica
    assert "num_documento" not in negocio                                      # la vista no la tenía


def test_idempotencia_byte_identical():
    """spec §7.6: mismo modelo + mismo ruleset ⇒ output byte-identical."""
    a = _run()
    b = _run()
    assert [s["sql"] for s in a["statements"]] == [s["sql"] for s in b["statements"]]


def test_el_motor_no_toca_la_bd():
    """spec §13: el módulo engine no abre NINGUNA conexión — ni siquiera
    importa el cliente de BD."""
    engine_dir = pathlib.Path("app/features/ddl_rules/engine")
    for f in engine_dir.glob("*.py"):
        src = f.read_text(encoding="utf-8")
        assert "get_db" not in src and "core.db" not in src, f"{f.name} toca la BD"


# ── /test e /impact (service, repos mockeados) ────────────────────────────

def test_service_test_rule_fragmentos(monkeypatch):
    from app.features.ddl_rules import service as svc
    monkeypatch.setattr(svc.udp_repo, "list_udp", AsyncMock(return_value=DEFS))
    monkeypatch.setattr(svc.repository, "get_config",
                        AsyncMock(return_value={"id": "global", **CONFIG}))
    monkeypatch.setattr(svc.dom_repo, "list_domains", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc.catalog_repo, "list_tables_by_ids", AsyncMock(return_value=[TABLE]))
    monkeypatch.setattr(svc.catalog_repo, "list_columns", AsyncMock(return_value=COLS))
    out = asyncio.run(svc.test_rule(RULES[0], "t1"))   # enmascarar_dac
    assert out["table"] == "core.tbl_cliente"
    assert out["matched"] == 2 and out["total"] == 4
    frags = {f["column"]: f for f in out["fragments"]}
    assert frags["nom_cliente"]["sql"] == "sha2(nom_cliente, 512) AS nom_cliente"
    assert frags["nom_cliente"]["why"] == "Clasificacion del Dato = 'DAC-NOMBRE'"


def test_service_impact_cuenta_columnas_y_tablas(monkeypatch):
    from app.features.ddl_rules import service as svc
    monkeypatch.setattr(svc.udp_repo, "list_udp", AsyncMock(return_value=DEFS))
    monkeypatch.setattr(svc.repository, "get_config",
                        AsyncMock(return_value={"id": "global", **CONFIG}))
    monkeypatch.setattr(svc.dom_repo, "list_domains", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc.repository, "tables_light",
                        AsyncMock(return_value=[{**TABLE},
                                                {"id": "t2", "physicalName": "tbl_x",
                                                 "schema": "core", "udpValues": {}}]))
    cols = [{**c, "tableId": "t1", "id": f"c{i}"} for i, c in enumerate(COLS)]
    monkeypatch.setattr(svc.repository, "columns_light", AsyncMock(return_value=cols))
    out = asyncio.run(svc.impact(RULES[0]))
    assert out == {"columns": 2, "tables": 1}
    tags_tabla = next(r for r in RULES if r["name"] == "tags_tabla")
    out_tab = asyncio.run(svc.impact(tags_tabla))      # target table
    assert out_tab == {"columns": 0, "tables": 1}      # t2 sin Dominio Principal


def test_templates_payload_resuelve_lookup(monkeypatch):
    from app.features.ddl_rules import service as svc
    monkeypatch.setattr(svc.udp_repo, "list_udp", AsyncMock(return_value=DEFS))
    out = asyncio.run(svc.templates_payload())
    assert len(out["templates"]) == 6 and len(out["seedRules"]) == 8
    assert out["seedLookups"]["vacuum_map"]["fromUdpId"] == "u-vac"
    assert out["seedLookups"]["dac_map"]["fromUdpId"] == "u-dac-col"
    assert out["seedLookups"]["dac_map"]["values"]["DAC-TARJETA"] == "TARJETA"
    assert "fromUdpName" not in out["seedLookups"]["vacuum_map"]
    # el import del módulo de plantillas queda intacto (deepcopy)
    assert "fromUdpName" in SEED_LOOKUPS["vacuum_map"]


def test_service_render_export_selecciona_reglas_y_estampa_version(monkeypatch):
    """El puente del export (doc 30 §8): solo corre las reglas SELECCIONADAS,
    devuelve la versión de standards usada y audita la corrida."""
    from app.features.ddl_rules import service as svc
    monkeypatch.setattr(svc.udp_repo, "list_udp", AsyncMock(return_value=DEFS))
    monkeypatch.setattr(svc.repository, "get_config",
                        AsyncMock(return_value={"id": "global", **CONFIG}))
    monkeypatch.setattr(svc.dom_repo, "list_domains", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc.repository, "list_rules", AsyncMock(return_value=RULES))
    monkeypatch.setattr(svc.std_repo, "list_versions",
                        AsyncMock(return_value=[{"label": "v12", "seq": 12}]))
    aud = AsyncMock()
    monkeypatch.setattr(svc, "audit", aud)
    desenc = next(r for r in RULES if r["name"] == "desencriptar_dac_negocio")
    body = {"ruleIds": [desenc["id"]],             # SOLO la de desencriptación
            "model": PAYLOAD["model"], "tables": PAYLOAD["tables"], "views": PAYLOAD["views"]}
    out = asyncio.run(svc.render_export_payload("maria.rojas", body))
    assert out["rulesetVersion"] == "v12" and out["applied"] >= 1
    arts = [s["artifact"] for s in out["statements"]]
    assert "ddl.tabla_rej" not in arts             # el generador no fue seleccionado
    negocio = next(s for s in out["statements"] if s["artifact"] == "ddl.vista_negocio")
    assert "bcp_ddv_desencrypt(nom_cliente, 'NOMBRE')" in negocio["sql"]
    aud.assert_awaited_once()
    assert aud.await_args.args[:2] == ("maria.rojas", "ddl.export_render")


def test_vista_no_negocio_pasa_intacta():
    """Una vista SIN 'on canvas' (businessView=False) no es vista de negocio:
    no se decora y su SQL sale byte-idéntico."""
    payload = {**PAYLOAD, "views": [{**PAYLOAD["views"][0], "businessView": False}]}
    out = pipeline.render_export(payload, RULES, CONFIG, DEFS, {})
    plain = next(s for s in out["statements"] if s["name"] == "clientes")
    assert plain["artifact"] == "ddl.vista" and plain["sql"] == VIEW_SQL


def test_seed_rules_validan_contra_el_catalogo_real():
    """Las 7 semillas deben salir VÁLIDAS con las defs reales + el lookup."""
    importlib.import_module("app.features.ddl_rules.engine.validate")
    from app.features.ddl_rules.engine import validate as v
    from app.features.ddl_rules import service as svc
    arts = [a["id"] for a in svc.artifact_catalog(RULES)]
    for seed in RULES:
        rep = v.validate_rule(seed, DEFS, CONFIG, arts)
        assert rep["state"] == "valid", f"{seed['name']}: {rep['errors']}"
