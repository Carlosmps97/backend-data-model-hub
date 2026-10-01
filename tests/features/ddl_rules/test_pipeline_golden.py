"""Golden end-to-end (doc 30 F6 · doc 76): el ruleset base (macro BCP) corriendo
completo por el pipeline sobre la tabla CLIENTE de ejemplo, + idempotencia
byte-identical (§7.6) + pureza del motor (§13: cero BD)."""
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
    {"id": "u-part", "name": "Particion", "level": "column", "dataType": "list",
     "allowedValues": ["No Definido", "PART_01", "PART_02", "PART_03"]},
]
_ID_BY_NAME = {(d["level"], d["name"]): d["id"] for d in DEFS}

# lookups como los persistiría el apply (fromUdpId resuelto).
LOOKUPS = {name: {**{k: v for k, v in lk.items() if k != "fromUdpName"},
                  "fromUdpId": _ID_BY_NAME[(lk["fromLevel"], lk["fromUdpName"])]}
           for name, lk in SEED_LOOKUPS.items()}
CONFIG = {"lookups": LOOKUPS, "functions": []}

# Las semillas tal cual quedarían guardadas (válidas).
RULES = [{**r, "id": f"seed-{i}", "validationState": "valid"} for i, r in enumerate(SEED_RULES)]
BY_NAME = {r["name"]: r for r in RULES}

TABLE = {"id": "t1", "physicalName": "tbl_cliente", "schema": "core",
         "udpValues": {"u-dom": "CLIENTES", "u-ec": "Migracion", "u-te": "Referencia",
                       "u-vac": "MONTHLY_90 days", "u-dac-tab": "DAC"}}
COLS = [
    {"physicalName": "cod_cliente", "dataType": "STRING", "ordinal": 0,
     "isNullable": False, "isPrimaryKey": True, "udpValues": {}},
    {"physicalName": "nom_cliente", "dataType": "STRING", "ordinal": 1,
     "udpValues": {"u-dac-col": "DAC-NOMBRE"}},
    {"physicalName": "num_documento", "dataType": "STRING", "ordinal": 2,
     "udpValues": {"u-dac-col": "DAC-DOCUMENTO"}},
    {"physicalName": "fec_alta", "dataType": "DATE", "ordinal": 3, "isPartition": True,
     "udpValues": {"u-dac-col": "No DAC", "u-part": "PART_01"}},
]
# Base emitida por el FRONT (doc 93: sin comillas, tipos en minúscula, OR REPLACE;
# doc 107: la partición solo en el PARTITIONED BY, con su tipo).
BASE_SQL = ("CREATE OR REPLACE TABLE core.tbl_cliente (\n"
            "  cod_cliente string,\n  nom_cliente string,\n"
            "  num_documento string\n)\nUSING DELTA\nPARTITIONED BY (fec_alta date);")
VIEW_SQL = ("CREATE OR REPLACE VIEW negocio.clientes AS\n"
            "SELECT\n  cod_cliente AS cod_cliente,\n  nom_cliente AS nom_cliente,\n"
            "  fec_alta AS fec_alta\nFROM core.tbl_cliente;")

PAYLOAD = {
    "model": {"name": "DDV", "udpValues": {}},
    "options": {"identifierCase": "lower"},
    "tables": [{"table": TABLE, "columns": COLS, "baseSql": BASE_SQL}],
    "views": [{"name": "clientes", "schema": "negocio", "sql": VIEW_SQL,
               "sourceTableIds": ["t1"]}],
}


def _run(payload=PAYLOAD):
    return pipeline.render_export(payload, RULES, CONFIG, DEFS, {})


def _objects(out):
    """Artefactos principales (sin los anexos .tags/.extra) en orden."""
    return [s["artifact"] for s in out["statements"] if not s["artifact"].endswith((".tags", ".extra"))]


def _addenda(out, artifact):
    return [s["sql"] for s in out["statements"] if s["artifact"] == f"{artifact}.tags"]


def test_pipeline_produce_todos_los_artefactos_en_orden():
    out = _run()
    assert _objects(out) == [
        "ddl.tabla_fisica",          # preámbulo + CREATE con TBLPROPERTIES inyectado
        "ddl.tabla_rej",             # generador (prio 200, 1ª pasada)
        "ddl.vista_tecnica",         # 180, 1ª pasada
        "ddl.vista_tecnica_dac",     # 170, 1ª pasada (la tabla es DAC)
        "ddl.vista_rej",             # 160, 2ª pasada (espera a la _rej)
        "ddl.vista_rej_dac",         # 150, 2ª pasada
        "ddl.vista_negocio",         # vista del payload decorada
    ]
    # Los anexos de cada objeto van justo detrás de él (orden de la macro:
    # tags de tabla → tags de columna).
    fisica = _addenda(out, "ddl.tabla_fisica")
    assert [s.split(" SET TAGS ")[0] for s in fisica] == [
        "ALTER TABLE core.tbl_cliente",                                # updateFrequency (prio 60)
        "ALTER TABLE core.tbl_cliente",                                # isDAC (prio 55)
        "ALTER TABLE core.tbl_cliente ALTER COLUMN nom_cliente",       # DAC por columna
        "ALTER TABLE core.tbl_cliente ALTER COLUMN num_documento",
    ]


def test_golden_fragmentos_clave():
    out = {s["artifact"]: s for s in _run()["statements"]}
    fisica = out["ddl.tabla_fisica"]["sql"]
    # preámbulo comentado (statements.before) + CREATE intacto + TBLPROPERTIES
    assert fisica.startswith("-- DROP TABLE IF EXISTS core.tbl_cliente;\n" + BASE_SQL[:-1])
    assert fisica.endswith("TBLPROPERTIES (\n  'delta.deletedFileRetentionDuration' = '90 days',\n"
                           "  'delta.logRetentionDuration' = '90 days'\n);")
    tags = _addenda(_run(), "ddl.tabla_fisica")
    assert tags[0] == "ALTER TABLE core.tbl_cliente SET TAGS ('updateFrequency' = 'MONTHLY');"
    assert tags[1] == "ALTER TABLE core.tbl_cliente SET TAGS ('isDAC' = 'True');"
    assert tags[2] == "ALTER TABLE core.tbl_cliente ALTER COLUMN nom_cliente SET TAGS ('DAC' = 'NOMBRE');"
    rej = out["ddl.tabla_rej"]["sql"]
    assert "core.tbl_cliente_rej" in rej and rej.startswith("-- DROP TABLE IF EXISTS core.tbl_cliente_rej;")
    # partición: conserva su tipo (keep_partition_type) y, como en la física, va
    # solo en el PARTITIONED BY (doc 107); la añadida cierra la lista
    assert "  tiporeject string\n)\nUSING DELTA\nPARTITIONED BY (fec_alta date)\n" in rej
    assert "  nom_cliente string," in rej and "NOT NULL" not in rej and "  fec_alta" not in rej
    assert "'delta.logRetentionDuration' = '90 days'" in rej
    vista_rej = out["ddl.vista_rej"]["sql"]
    assert "core_v.tbl_cliente_rej" in vista_rej and "decrypt_column_view" not in vista_rej   # §7.4
    assert "nom_cliente" not in vista_rej                                       # DAC excluida (tabla DAC)
    assert vista_rej.rstrip(";").endswith("tiporeject AS tiporeject\nFROM core.tbl_cliente_rej")
    tecnica = out["ddl.vista_tecnica"]["sql"]
    assert tecnica == ("CREATE OR REPLACE VIEW core_v.tbl_cliente AS\nSELECT\n"
                       "  cod_cliente AS cod_cliente,\n  fec_alta AS fec_alta\nFROM core.tbl_cliente;")
    assert _addenda(_run(), "ddl.vista_tecnica") == [
        "ALTER TABLE core_v.tbl_cliente SET TAGS ('updateFrequency' = 'MONTHLY');",
        "ALTER TABLE core_v.tbl_cliente SET TAGS ('isDAC' = 'False');",
    ]
    tecnica_dac = out["ddl.vista_tecnica_dac"]["sql"]
    assert "CREATE OR REPLACE VIEW core_v.tbl_clientedac AS" in tecnica_dac
    assert "bcp_encrypt_function.decrypt_column_view(nom_cliente, 'NOMBRE') AS nom_cliente" in tecnica_dac
    assert "decrypt_column_view(fec_alta" not in tecnica_dac                     # 'No DAC' no dispara
    assert _addenda(_run(), "ddl.vista_tecnica_dac")[1:] == [
        "ALTER TABLE core_v.tbl_clientedac SET TAGS ('isDAC' = 'True');",
        "ALTER TABLE core_v.tbl_clientedac ALTER COLUMN nom_cliente SET TAGS ('DAC' = 'NOMBRE');",
        "ALTER TABLE core_v.tbl_clientedac ALTER COLUMN num_documento SET TAGS ('DAC' = 'DOCUMENTO');",
    ]
    # Vista de NEGOCIO: la columna DAC sale con la función de desencriptación y
    # el sufijo del valor UDP (DAC-NOMBRE → 'NOMBRE'); tags de objeto y de columna.
    negocio = out["ddl.vista_negocio"]["sql"]
    assert "bcp_encrypt_function.decrypt_column_view(nom_cliente, 'NOMBRE') AS nom_cliente" in negocio
    assert "num_documento" not in negocio                                      # la vista no la tenía
    assert _addenda(_run(), "ddl.vista_negocio") == [
        "ALTER TABLE negocio.clientes SET TAGS ('updateFrequency' = 'MONTHLY');",
        "ALTER TABLE negocio.clientes SET TAGS ('isDAC' = 'True');",
        "ALTER TABLE negocio.clientes ALTER COLUMN nom_cliente SET TAGS ('DAC' = 'NOMBRE');",
    ]


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

def _mock_service(monkeypatch):
    from app.features.ddl_rules import service as svc
    monkeypatch.setattr(svc.udp_repo, "list_udp", AsyncMock(return_value=DEFS))
    monkeypatch.setattr(svc.repository, "get_config",
                        AsyncMock(return_value={"id": "global", **CONFIG}))
    monkeypatch.setattr(svc.repository, "list_rules", AsyncMock(return_value=RULES))
    monkeypatch.setattr(svc.dom_repo, "list_domains", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc.catalog_repo, "list_tables_by_ids", AsyncMock(return_value=[TABLE]))
    monkeypatch.setattr(svc.catalog_repo, "list_columns", AsyncMock(return_value=COLS))
    return svc


def test_service_test_rule_fragmentos(monkeypatch):
    svc = _mock_service(monkeypatch)
    out = asyncio.run(svc.test_rule("p1", BY_NAME["desencriptar_dac"], "t1"))
    assert out["table"] == "core.tbl_cliente"
    assert out["matched"] == 2 and out["total"] == 4
    frags = {f["column"]: f for f in out["fragments"]}
    assert frags["nom_cliente"]["sql"] == "bcp_encrypt_function.decrypt_column_view(nom_cliente, 'NOMBRE') AS nom_cliente"
    assert frags["nom_cliente"]["why"] == "Clasificacion del Dato = 'DAC-NOMBRE'"
    # exclude → fragmento explícito; tags de objeto sobre artefacto VISTA → ALTER TABLE (doc 93 D6)
    out_ex = asyncio.run(svc.test_rule("p1", BY_NAME["excluir_dac_vista_sin_dac"], "t1"))
    assert {f["column"] for f in out_ex["fragments"]} == {"nom_cliente", "num_documento"}
    assert all(f["sql"] == "-- excluded from the SELECT" for f in out_ex["fragments"])
    out_tag = asyncio.run(svc.test_rule("p1", BY_NAME["tags_isdac_sin_dac"], "t1"))
    assert out_tag["fragments"][0]["sql"] == "ALTER TABLE core.tbl_cliente SET TAGS ('isDAC' = 'False');"
    out_drop = asyncio.run(svc.test_rule("p1", BY_NAME["drop_comentado"], "t1"))
    assert out_drop["fragments"][0]["sql"] == "-- DROP TABLE IF EXISTS core.tbl_cliente;"


def test_service_impact_cuenta_columnas_y_tablas(monkeypatch):
    svc = _mock_service(monkeypatch)
    monkeypatch.setattr(svc.repository, "tables_light",
                        AsyncMock(return_value=[{**TABLE},
                                                {"id": "t2", "physicalName": "tbl_x",
                                                 "schema": "core", "udpValues": {}}]))
    cols = [{**c, "tableId": "t1", "id": f"c{i}"} for i, c in enumerate(COLS)]
    monkeypatch.setattr(svc.repository, "columns_light", AsyncMock(return_value=cols))
    out = asyncio.run(svc.impact("p1", BY_NAME["desencriptar_dac"]))
    assert out == {"columns": 2, "tables": 1}
    out_gen = asyncio.run(svc.impact("p1", BY_NAME["vista_tecnica_dac"]))   # solo tablas DAC
    assert out_gen == {"columns": 0, "tables": 1}
    out_all = asyncio.run(svc.impact("p1", BY_NAME["tags_isdac"]))          # condición abierta
    assert out_all == {"columns": 0, "tables": 2}


def test_templates_payload_resuelve_lookup(monkeypatch):
    from app.features.ddl_rules import service as svc
    monkeypatch.setattr(svc.udp_repo, "list_udp", AsyncMock(return_value=DEFS))
    out = asyncio.run(svc.templates_payload("p1"))
    assert len(out["templates"]) == 10 and len(out["seedRules"]) == 17   # doc 93: 12 reglas + 5 generadores
    assert out["seedLookups"]["vacuum_map"]["fromUdpId"] == "u-vac"
    assert out["seedLookups"]["update_frequency_map"]["fromUdpId"] == "u-vac"
    assert out["seedLookups"]["dac_flag_map"]["fromUdpId"] == "u-dac-tab"
    assert out["seedLookups"]["dac_map"]["fromUdpId"] == "u-dac-col"
    assert out["seedLookups"]["dac_map"]["values"]["DAC-TARJETA"] == "TARJETA"
    assert out["seedOutput"]["fileNames"]["modeledView"] == "VIEW_NEG"        # doc 93 D1
    assert out["seedLookups"]["vacuum_map"]["values"]["DAILY_15 days"] == "15 days"
    assert out["seedLookups"]["update_frequency_map"]["values"]["DAILY_15 days"] == "DAILY"
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
    body = {"ruleIds": [BY_NAME["desencriptar_dac"]["id"]],   # SOLO la de desencriptación
            "model": PAYLOAD["model"], "tables": PAYLOAD["tables"], "views": PAYLOAD["views"]}
    out = asyncio.run(svc.render_export_payload("maria.rojas", "p1", body))
    assert out["rulesetVersion"] == "v12" and out["applied"] >= 1
    arts = [s["artifact"] for s in out["statements"]]
    assert "ddl.tabla_rej" not in arts             # el generador no fue seleccionado
    negocio = next(s for s in out["statements"] if s["artifact"] == "ddl.vista_negocio")
    assert "bcp_encrypt_function.decrypt_column_view(nom_cliente, 'NOMBRE')" in negocio["sql"]
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
    """Las 17 semillas deben salir VÁLIDAS con las defs reales + los lookups."""
    importlib.import_module("app.features.ddl_rules.engine.validate")
    from app.features.ddl_rules.engine import validate as v
    from app.features.ddl_rules import service as svc
    arts = [a["id"] for a in svc.artifact_catalog(RULES)]
    kinds = svc.artifact_kinds(RULES)
    for seed in RULES:
        rep = v.validate_rule(seed, DEFS, CONFIG, arts, kinds)
        assert rep["state"] == "valid", f"{seed['name']}: {rep['errors']}"
        assert not any("artifact —" in w for w in rep["warnings"]), f"{seed['name']}: {rep['warnings']}"
