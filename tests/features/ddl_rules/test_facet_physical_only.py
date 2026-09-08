"""Doc 69 §4.4: el DDL es físico — `tabla.udp[...]`/`columna.udp[...]` y las
reglas resuelven SOLO defs físicas; un homónimo lógico jamás pisa el valor."""
from __future__ import annotations

from app.features.ddl_rules.engine import validate as v
from app.features.ddl_rules.engine.context import column_ctx, names_by_id

DEFS = [{"id": "u-phys", "name": "Clasificacion del Dato", "level": "column"},
        {"id": "u-log", "name": "Clasificacion del Dato", "level": "column", "view": "logical"}]


def test_names_by_id_excluye_logicas():
    assert names_by_id(DEFS) == {"u-phys": "Clasificacion del Dato"}


def test_column_ctx_toma_el_valor_fisico_aunque_el_logico_difiera():
    ctx = column_ctx({"physicalName": "C", "dataType": "STRING",
                      "udpValues": {"u-log": "No DAC", "u-phys": "DAC-NOMBRE"}}, names_by_id(DEFS))
    assert ctx["udp"] == {"Clasificacion del Dato": "DAC-NOMBRE"}


def test_defs_by_level_ignora_logicas_aunque_vengan_despues():
    by = v._defs_by_level(DEFS)
    assert by["column"]["Clasificacion del Dato"]["id"] == "u-phys"


def test_validate_rule_enlaza_el_udp_fisico():
    rule = {"name": "r", "kind": "rule", "target": "column", "udpRefs": [], "action": {},
            "condition": 'columna.udp["Clasificacion del Dato"] LIKE \'DAC-%\''}
    rep = v.validate_rule(rule, DEFS, {"lookups": {}, "functions": []}, [])
    assert {"udpId": "u-phys", "level": "column"} in rep["udpRefs"]
    assert not any(r["udpId"] == "u-log" for r in rep["udpRefs"])
