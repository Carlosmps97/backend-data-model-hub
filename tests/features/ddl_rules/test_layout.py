"""Doc 73 · Column layout: `action.layout = {partitionColumns: 'last'}` emite
las columnas de partición al FINAL del CREATE (físico en el front, tablas
generadas en el motor) sin tocar el orden físico del modelo. Validación,
opciones efectivas del pipeline y /test."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.ddl_rules.engine import generators as g
from app.features.ddl_rules.engine import pipeline
from app.features.ddl_rules.engine import validate as v
from app.features.ddl_rules.engine.context import column_ctx, names_by_id, table_ctx
from app.features.ddl_rules.templates import SEED_RULES, TEMPLATES

LAYOUT = {"id": "l1", "name": "particiones_al_final", "kind": "rule", "target": "table",
          "condition": "", "action": {"layout": {"partitionColumns": "last"}},
          "appliesTo": ["ddl.tabla_fisica"], "priority": 30, "enabled": True, "validationState": "valid"}
REJ = {"id": "g1", "name": "tabla_rechazos", "kind": "generator", "sourceArtifact": "ddl.tabla_fisica",
       "condition": "true", "priority": 200, "enabled": True, "validationState": "valid",
       "action": {"emit": {"artifact": "ddl.tabla_rej", "type": "table", "schema": "{tabla.esquema}",
                           "name": "{tabla.nombre}_rej", "columns": {"inherit": "all", "force_type": "STRING",
                                                                    "strip": ["not_null", "pk"]}}}}
TABLE = {"id": "t1", "physicalName": "tbl_x", "schema": "core", "udpValues": {}}
# orden físico: codmes (partición) va PRIMERA; fecdia (partición) en el medio.
COLS = [
    {"physicalName": "codmes", "dataType": "INT", "ordinal": 0, "isPartition": True, "udpValues": {}},
    {"physicalName": "cod_cli", "dataType": "STRING", "ordinal": 1, "isPrimaryKey": True, "isNullable": False, "udpValues": {}},
    {"physicalName": "fecdia", "dataType": "DATE", "ordinal": 2, "isPartition": True, "udpValues": {}},
    {"physicalName": "monto", "dataType": "DECIMAL(18,2)", "ordinal": 3, "udpValues": {}},
]
N = names_by_id([])
BASE = {"tabla": table_ctx(TABLE, N), "modelo": {"nombre": "DDV", "udp": {}}}
CTX = {c["physicalName"]: column_ctx(c, N) for c in COLS}


def test_layout_from_rules_solo_reglas_de_tabla_sobre_la_fisica():
    assert g.layout_from_rules([LAYOUT]) == {"partitionsLast": True}
    assert g.layout_from_rules([{**LAYOUT, "appliesTo": ["ddl.tabla_rej"]}]) == {"partitionsLast": False}
    assert g.layout_from_rules([{**LAYOUT, "action": {"layout": {"partitionColumns": "keep"}}}]) == {"partitionsLast": False}
    assert g.layout_from_rules([REJ]) == {"partitionsLast": False}


def test_layout_columns_estable_y_sin_opcion_intacto():
    cols = [{"name": "a", "partition": True}, {"name": "b"}, {"name": "c", "partition": True}, {"name": "d"}]
    assert [c["name"] for c in g.layout_columns(cols, {"partitionsLast": True})] == ["b", "d", "a", "c"]
    assert [c["name"] for c in g.layout_columns(cols, None)] == ["a", "b", "c", "d"]


def test_generador_orden_fisico_explicito_y_particiones_al_final_siempre():
    # columnas de ENTRADA desordenadas a propósito: el motor ordena por `ordinal`
    # (doc 73 O2) y las tablas generadas emiten las particiones al final SIEMPRE
    # (pedido owner 07-20) — con o sin la regla de layout.
    shuffled = [COLS[2], COLS[0], COLS[3], COLS[1]]
    for options in ({}, {"partitionsLast": True}):
        stmts, _ = g.run_generators([REJ], TABLE, shuffled, BASE, CTX, {}, options)
        sql = stmts[0]["sql"]
        body = sql.split("(\n", 1)[1].split("\n)", 1)[0]
        assert [line.split()[0].strip("`") for line in body.split(",\n")] == ["cod_cli", "monto", "codmes", "fecdia"]
        assert "PARTITIONED BY (`codmes`, `fecdia`)" in sql        # la cláusula conserva el orden físico


def test_pipeline_deriva_el_layout_de_las_reglas_que_corren():
    payload = {"model": {"name": "DDV", "udpValues": {}}, "options": {},
               "tables": [{"table": TABLE, "columns": COLS, "baseSql": "CREATE TABLE core.tbl_x (x INT);"}],
               "views": []}
    out = pipeline.render_export(payload, [LAYOUT, REJ], {"lookups": {}, "functions": []}, [], {})
    # la regla de layout no emite sentencias propias: queda en el log como aplicada
    assert [s["artifact"] for s in out["statements"]] == ["ddl.tabla_fisica", "ddl.tabla_rej"]
    applied = [e for e in out["log"] if e.get("rule") == "particiones_al_final"]
    assert applied and applied[0]["status"] == "applied" and "partition columns last" in applied[0]["object"]
    # deshabilitada → skipped, no aplicada
    out2 = pipeline.render_export(payload, [{**LAYOUT, "enabled": False}, REJ], {"lookups": {}, "functions": []}, [], {})
    assert [e["status"] for e in out2["log"] if e.get("rule") == "particiones_al_final"] == ["skipped"]


ARTS = ["ddl.tabla_fisica", "ddl.vista_negocio", "ddl.tabla_rej"]


def test_validate_layout_ok_y_errores_estructurales():
    ok = v.validate_rule(LAYOUT, [], {}, ARTS, {"ddl.tabla_fisica": "table"})
    assert ok["state"] == "valid" and ok["errors"] == []
    bad = v.validate_rule({**LAYOUT, "condition": "tabla.tipo = 'physical'", "target": "column",
                           "action": {"layout": {"partitionColumns": "first", "zzz": 1}, "tags": {"a": "b"}},
                           "appliesTo": ["ddl.tabla_fisica", "ddl.tabla_rej"]}, [], {}, ARTS, {})
    msgs = " | ".join(e["message"] for e in bad["errors"])
    assert bad["state"] == "invalid"
    assert "must be one of: keep, last" in msgs and "Unknown column layout setting 'zzz'" in msgs
    assert "table rule" in msgs and "leave the condition empty" in msgs and "can't be combined" in msgs
    assert any("only applies to the physical table" in w for w in bad["warnings"])
    empty = v.validate_rule({**LAYOUT, "action": {"layout": {}}}, [], {}, ARTS, {})
    assert empty["state"] == "invalid"


def test_semilla_y_plantilla_de_layout():
    seed = next(r for r in SEED_RULES if r["name"] == "particiones_al_final")
    assert seed["action"] == {"layout": {"partitionColumns": "last"}} and seed["condition"] == ""
    assert v.validate_rule({**seed, "id": None, "udpRefs": []}, [], {}, ARTS, {})["state"] == "valid"
    assert any(t["id"] == "partitions-last" for t in TEMPLATES)


def test_service_test_rule_layout(monkeypatch):
    from app.features.ddl_rules import service as svc
    monkeypatch.setattr(svc.udp_repo, "list_udp", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc.repository, "get_config", AsyncMock(return_value={"id": "global", "lookups": {}, "functions": []}))
    monkeypatch.setattr(svc.dom_repo, "list_domains", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc.catalog_repo, "list_tables_by_ids", AsyncMock(return_value=[TABLE]))
    monkeypatch.setattr(svc.catalog_repo, "list_columns", AsyncMock(return_value=COLS))
    out = asyncio.run(svc.test_rule("p1", LAYOUT, "t1"))
    assert out["matched"] == 1
    frag = out["fragments"][0]
    assert frag["sql"] == "-- column order: cod_cli, monto, codmes, fecdia"
    assert frag["why"] == "partition columns last: codmes, fecdia"
