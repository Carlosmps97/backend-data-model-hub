"""Doc 78 §4: forma del perfil, catálogo fijo y validación estructural (pura)."""
from __future__ import annotations

import copy

from app.features.bulk_upload.profiles.models import (
    FIELDS_BY_ROLE, catalog, key_field, validate_profile,
)

DEFS = [{"id": "t-p", "name": "Tipo de Entidad", "level": "table", "view": "physical"},
        {"id": "t-l", "name": "Tipo de Entidad", "level": "table", "view": "logical"},
        {"id": "c-p", "name": "Campo Cross", "level": "column", "view": "physical"}]

GOOD = {
    "name": "Plantilla BCP", "isDefault": True,
    "sheets": {
        "tables": {"name": "Cargar_Tablas", "required": True, "headerRow": 5, "mappings": [
            {"header": "TABLA_LOGICO", "target": {"kind": "field", "field": "logicalName"},
             "rules": [{"type": "required", "severity": "error"}, {"type": "maxLength", "value": 80, "severity": "warning"}]},
            {"header": "ESQUEMA", "target": {"kind": "field", "field": "schema"}, "rules": [{"type": "mustExist", "severity": "error"}]},
            {"header": "UDP_Tipo_de_Entidad", "target": {"kind": "udp", "udpIds": ["t-p", "t-l"]}},
            {"header": "LOGICO", "target": {"kind": "ignore"}}]},
        "columns": {"name": "Cargar_Campos", "required": True, "headerRow": 5, "mappings": [
            {"header": "TABLA_LOGICO", "target": {"kind": "field", "field": "tableRef"}},
            {"header": "CAMPO_LOGICO", "target": {"kind": "field", "field": "logicalName"}},
            {"header": "UDP Campo Cross", "target": {"kind": "udp", "udpIds": ["c-p"]}}]},
    },
    "policies": {"onExistingTable": "update", "onExistingColumn": "reject", "unknownHeaders": "warn"},
}


def _with(path, value):
    p = copy.deepcopy(GOOD)
    node = p
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value
    return p


def _codes(profile):
    return sorted(pr["code"] for pr in validate_profile(profile, DEFS))


def test_catalogo_fijo():
    cat = catalog()
    assert [f["field"] for f in cat["fields"]["tables"]][:2] == ["logicalName", "physicalName"]
    assert key_field("tables") == "logicalName" and key_field("columns") == "tableRef"
    assert FIELDS_BY_ROLE["columns"]["parentDomain"]["mustExist"] is True
    assert {r["type"] for r in cat["ruleTypes"]} == {"required", "maxLength", "pattern", "allowedValues", "uniqueInFile", "mustExist"}
    assert cat["policies"]["unknownHeaders"] == ["warn", "ignore", "reject"]


def test_perfil_valido_no_tiene_problemas():
    assert validate_profile(GOOD, DEFS) == []


def test_nombre_y_hojas():
    assert "name-empty" in _codes(_with(["name"], "  "))
    assert "sheet-names-equal" in _codes(_with(["sheets", "columns", "name"], "cargar_tablas"))
    assert "sheet-name-empty" in _codes(_with(["sheets", "columns", "name"], ""))
    assert "header-row-invalid" in _codes(_with(["sheets", "tables", "headerRow"], 0))
    assert "sheet-missing" in _codes(_with(["sheets"], {"tables": GOOD["sheets"]["tables"]}))


def test_cabeceras_y_campos():
    dup = _with(["sheets", "tables", "mappings", 3], {"header": "tabla lógico", "target": {"kind": "ignore"}})
    assert "header-duplicate" in _codes(dup)
    assert "header-empty" in _codes(_with(["sheets", "tables", "mappings", 3, "header"], " "))
    assert "field-unknown" in _codes(_with(["sheets", "tables", "mappings", 1, "target"], {"kind": "field", "field": "nope"}))
    assert "field-duplicate" in _codes(_with(["sheets", "tables", "mappings", 1, "target"], {"kind": "field", "field": "logicalName"}))
    missing = _with(["sheets", "columns", "mappings"], [{"header": "TABLA_LOGICO", "target": {"kind": "field", "field": "tableRef"}}])
    assert "field-required-missing" in _codes(missing)
    assert "target-invalid" in _codes(_with(["sheets", "tables", "mappings", 3, "target"], {"kind": "x"}))


def test_udp():
    assert "udp-empty" in _codes(_with(["sheets", "tables", "mappings", 2, "target"], {"kind": "udp", "udpIds": []}))
    assert "udp-unknown" in _codes(_with(["sheets", "tables", "mappings", 2, "target"], {"kind": "udp", "udpIds": ["zz"]}))
    assert "udp-wrong-level" in _codes(_with(["sheets", "tables", "mappings", 2, "target"], {"kind": "udp", "udpIds": ["c-p"]}))
    twice = _with(["sheets", "tables", "mappings", 3], {"header": "OTRA", "target": {"kind": "udp", "udpIds": ["t-p"]}})
    assert "udp-duplicate" in _codes(twice)


def test_reglas_y_defaults():
    m = ["sheets", "tables", "mappings", 0, "rules"]
    assert "rule-unknown" in _codes(_with(m, [{"type": "nope"}]))
    assert "rule-duplicate" in _codes(_with(m, [{"type": "required"}, {"type": "required"}]))
    assert "rule-value-invalid" in _codes(_with(m, [{"type": "maxLength", "value": 0}]))
    assert "rule-value-invalid" in _codes(_with(m, [{"type": "maxLength", "value": True}]))
    assert "rule-value-invalid" in _codes(_with(m, [{"type": "pattern", "value": "("}]))
    assert "rule-value-invalid" in _codes(_with(m, [{"type": "pattern", "value": "a" * 201}]))
    assert "rule-value-invalid" in _codes(_with(m, [{"type": "allowedValues", "value": []}]))
    assert "rule-value-invalid" in _codes(_with(m, [{"type": "required", "severity": "fatal"}]))
    assert "rule-not-applicable" in _codes(_with(m, [{"type": "mustExist"}]))          # logicalName no admite mustExist
    assert "default-not-applicable" in _codes(_with(["sheets", "tables", "mappings", 0, "defaultValue"], "X"))
    assert validate_profile(_with(["sheets", "tables", "mappings", 1, "defaultValue"], "bcp_ddv"), DEFS) == []
    assert validate_profile(_with(m, [{"type": "pattern", "value": "[A-Z_]+"}, {"type": "allowedValues", "value": ["a", "b"]}]), DEFS) == []


def test_politicas():
    assert "policy-invalid" in _codes(_with(["policies", "unknownHeaders"], "explode"))
    p = _with(["policies"], {})
    assert validate_profile(p, DEFS) == []            # defaults implícitos


def test_paths_apuntan_a_la_fila():
    p = _with(["sheets", "tables", "mappings", 2, "target"], {"kind": "udp", "udpIds": ["zz"]})
    (pr,) = validate_profile(p, DEFS)
    assert pr["path"] == "sheets.tables.mappings[2].target"
