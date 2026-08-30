"""Reporte de validación (doc 55 §5): incidencias con severidad, tope por
severidad con totales, y forma final que consume el popup."""
from __future__ import annotations

from app.features.bulk_upload.report import SEVERITY_CAP, Issue, ReportBuilder


def test_cuenta_todo_pero_lista_hasta_el_tope():
    rb = ReportBuilder()
    for i in range(SEVERITY_CAP + 100):
        rb.error("Tablas", "missing-required", f"fila {i}", row=i + 3, column="TABLA_LOGICO")
    rb.warning("Atributos", "existing-column", "cambia", row=9)
    out = rb.build(summary={}, tables=[])
    assert out["errorCount"] == SEVERITY_CAP + 100
    assert len(out["errors"]) == SEVERITY_CAP
    assert out["warningCount"] == 1 and out["warnings"] == [
        {"severity": "warning", "sheet": "Atributos", "row": 9, "column": None,
         "code": "existing-column", "message": "cambia"}]


def test_has_errors_solo_con_errores():
    rb = ReportBuilder()
    assert rb.has_errors is False
    rb.warning("Tablas", "no-canvas", "x")
    assert rb.has_errors is False
    rb.add(Issue("error", "Tablas", None, None, "missing-sheet", "y"))
    assert rb.has_errors is True


def test_build_lleva_summary_y_tables_tal_cual():
    rb = ReportBuilder()
    out = rb.build(summary={"tables": {"create": 1, "update": 0, "unchanged": 0}},
                   tables=[{"row": 3, "logicalName": "A"}])
    assert out["summary"] == {"tables": {"create": 1, "update": 0, "unchanged": 0}}
    assert out["tables"] == [{"row": 3, "logicalName": "A"}]
    assert out["errors"] == [] and out["warnings"] == []


def test_issues_in_cuenta_incidencias_por_hoja_y_filas():
    # Alimenta el desglose por tabla del popup: incidencias de la fila de
    # `Tablas` + las de sus filas de `Atributos`.
    rb = ReportBuilder()
    rb.error("Tablas", "missing-required", "x", row=3)
    rb.warning("Atributos", "existing-column", "y", row=10)
    rb.warning("Atributos", "existing-column", "z", row=11)
    rb.warning("Atributos", "existing-column", "w", row=99)
    assert rb.issues_in("Tablas", [3]) == 1
    assert rb.issues_in("Atributos", [10, 11]) == 2
    assert rb.issues_in("Tablas", []) == 0
