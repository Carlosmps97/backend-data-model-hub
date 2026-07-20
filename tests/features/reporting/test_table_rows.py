"""Agregación pura del reporte (`reporting.service`)."""
from __future__ import annotations

from app.features.reporting.service import column_rows, table_rows


def _fixture():
    tables = [
        {"id": "t1", "physicalName": "CLIENTE", "logicalName": "Cliente",
         "schema": "core", "description": "Maestro de clientes"},
        {"id": "t2", "physicalName": "CUENTA", "logicalName": "Cuenta",
         "schema": "core", "description": None},
        {"id": "t3", "physicalName": "RIESGO", "logicalName": "Riesgo",
         "schema": "risk", "description": None},
    ]
    # columnCounts YA agregado por tabla (repository.column_counts vía $group):
    # t1=2, t2=1, t3 sin columnas.
    column_counts = {"t1": 2, "t2": 1}
    relationships = [
        {"parentTableId": "t1", "childTableId": "t2"},  # t1+1, t2+1
        {"parentTableId": "t2", "childTableId": "t3"},  # t2+1, t3+1
    ]
    subject_areas = [
        {"id": "sa1", "projectId": "p1", "name": "Banking", "tableIds": ["t1", "t2"]},
        {"id": "sa2", "projectId": "p2", "name": "Risk", "tableIds": ["t2", "t3"]},
    ]
    projects = [{"id": "p1", "name": "Core Banking"}, {"id": "p2", "name": "Risk Analytics"}]
    return tables, column_counts, relationships, subject_areas, projects


def test_table_rows_counts_and_names():
    rows = table_rows(*_fixture())
    by_id = {r["id"]: r for r in rows}

    # columnCount
    assert by_id["t1"]["columnCount"] == 2
    assert by_id["t2"]["columnCount"] == 1
    assert by_id["t3"]["columnCount"] == 0

    # relationshipCount = source O target
    assert by_id["t1"]["relationshipCount"] == 1
    assert by_id["t2"]["relationshipCount"] == 2
    assert by_id["t3"]["relationshipCount"] == 1

    # subjectAreas / projects = nombres que referencian la tabla (vía tableIds)
    assert by_id["t2"]["subjectAreas"] == ["Banking", "Risk"]
    assert by_id["t2"]["projects"] == ["Core Banking", "Risk Analytics"]
    assert by_id["t1"]["subjectAreas"] == ["Banking"]
    assert by_id["t1"]["projects"] == ["Core Banking"]

    # passthrough de campos de tabla
    assert by_id["t1"]["description"] == "Maestro de clientes"
    assert by_id["t1"]["schema"] == "core"


def test_table_rows_sorted_by_physical_name():
    rows = table_rows(*_fixture())
    assert [r["physicalName"] for r in rows] == ["CLIENTE", "CUENTA", "RIESGO"]


def test_table_rows_filter_by_schema():
    rows = table_rows(*_fixture(), filters={"schema": "core"})
    assert {r["id"] for r in rows} == {"t1", "t2"}


def test_table_rows_filter_by_project():
    # p2 referencia t2 y t3 (canvas "Risk").
    rows = table_rows(*_fixture(), filters={"projectId": "p2"})
    assert {r["id"] for r in rows} == {"t2", "t3"}


def test_table_rows_filter_combined_schema_and_project():
    # p2 → {t2, t3}; schema=risk → {t3}. Intersección = {t3}.
    rows = table_rows(*_fixture(), filters={"projectId": "p2", "schema": "risk"})
    assert {r["id"] for r in rows} == {"t3"}


def test_table_with_no_references_has_empty_lists():
    tables = [{"id": "t9", "physicalName": "ORPHAN", "logicalName": "Orphan"}]
    rows = table_rows(tables, {}, [], [], [])
    assert rows[0]["subjectAreas"] == []
    assert rows[0]["projects"] == []
    assert rows[0]["columnCount"] == 0
    assert rows[0]["relationshipCount"] == 0


def test_column_rows_resolves_domain_name_and_sorts():
    columns = [
        {"tableId": "t1", "physicalName": "ID_CLIENTE", "logicalName": "Id Cliente",
         "dataType": "BIGINT", "isPrimaryKey": True, "ordinal": 1},
        {"tableId": "t1", "physicalName": "MTO", "logicalName": "Monto",
         "dataType": "DECIMAL(18,2)", "parentDomainId": "d1", "ordinal": 0},
    ]
    parent_domains = [{"id": "d1", "name": "Importe"}]
    rows = column_rows(columns, parent_domains)

    # ordenadas por (tableId, ordinal): MTO (0) antes que ID_CLIENTE (1)
    assert [r["physicalName"] for r in rows] == ["MTO", "ID_CLIENTE"]
    assert rows[0]["parentDomain"] == "Importe"
    assert rows[1]["parentDomain"] is None
    assert rows[1]["isPrimaryKey"] is True
    # defaults aplicados
    assert rows[0]["isNullable"] is True
    assert rows[0]["isForeignKey"] is False
    assert rows[0]["isPartition"] is False
