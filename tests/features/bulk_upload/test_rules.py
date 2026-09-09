"""Doc 78 §3.5: cada regla, su severidad y la grafía canónica de allowedValues."""
from __future__ import annotations

from app.features.bulk_upload.profiles.rules import check_rules

M = {"header": "ESQUEMA", "target": {"kind": "field", "field": "schema"}}


def _run(rules, cells):
    issues, canon = check_rules({**M, "rules": rules}, cells, "tables", "Cargar_Tablas")
    return [(i.severity, i.code, i.row, i.column) for i in issues], canon


def test_required_max_length_pattern():
    issues, _ = _run([{"type": "required"}, {"type": "maxLength", "value": 3, "severity": "warning"}, {"type": "pattern", "value": "[a-z_]+"}],
                     [(6, ""), (7, "abcd"), (8, "ab1")])
    assert issues == [("error", "rule-required", 6, "ESQUEMA"), ("warning", "rule-max-length", 7, "ESQUEMA"),
                      ("error", "rule-pattern", 8, "ESQUEMA")]          # 'abcd' cumple el patrón; 'ab1' no


def test_allowed_values_canoniza_y_unique():
    issues, canon = _run([{"type": "allowedValues", "value": ["bcp_ddv", "bcp_udv"]}, {"type": "uniqueInFile"}],
                         [(6, "BCP_DDV"), (7, "bcp_ddv"), (8, "otro")])
    assert canon == {6: "bcp_ddv", 7: "bcp_ddv"}
    assert issues == [("error", "rule-allowed-values", 8, "ESQUEMA"), ("error", "rule-unique", 7, "ESQUEMA")]


def test_must_exist_no_se_evalua_aca_y_las_hojas_llevan_su_nombre():
    issues, _ = check_rules({**M, "rules": [{"type": "mustExist"}]}, [(6, "x")], "tables", "Cargar_Tablas")
    assert issues == []
    issues, _ = check_rules({**M, "rules": [{"type": "required"}]}, [(6, " ")], "tables", "Cargar_Tablas")
    assert issues[0].sheet == "Cargar_Tablas" and "ESQUEMA" in issues[0].message
