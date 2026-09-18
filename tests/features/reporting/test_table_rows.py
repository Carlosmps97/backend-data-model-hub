"""Agregación pura del reporte (`reporting.service`)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.reporting import service
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
    # Doc 88 §7: el subject area es la CARPETA del canvas (sa1/sa2 cuelgan de
    # carpetas; sa3 está en la raíz y no aporta subject area, sólo diagrama).
    subject_areas = [
        {"id": "sa1", "projectId": "p1", "name": "Banking", "tableIds": ["t1", "t2"], "folderId": "f-retail"},
        {"id": "sa2", "projectId": "p1", "name": "Risk", "tableIds": ["t2", "t3"], "folderId": "f-risk"},
        {"id": "sa3", "projectId": "p1", "name": "Scratch", "tableIds": ["t3"], "folderId": None},
    ]
    projects = [{"id": "p1", "name": "Core Banking"}]      # doc 75: el reporte es de UN proyecto
    return tables, column_counts, relationships, subject_areas, projects


FOLDERS = [{"id": "f-retail", "name": "1. Retail", "parentFolderId": "f-root"},
           {"id": "f-risk", "name": "3. Riesgos", "parentFolderId": "f-root"},
           {"id": "f-root", "name": "CPYBCA", "parentFolderId": None}]


def test_table_rows_counts_and_names():
    rows = table_rows(*_fixture(), folders=FOLDERS)
    by_id = {r["id"]: r for r in rows}

    # columnCount
    assert by_id["t1"]["columnCount"] == 2
    assert by_id["t2"]["columnCount"] == 1
    assert by_id["t3"]["columnCount"] == 0

    # relationshipCount = source O target
    assert by_id["t1"]["relationshipCount"] == 1
    assert by_id["t2"]["relationshipCount"] == 2
    assert by_id["t3"]["relationshipCount"] == 1

    # Doc 88 §7: subjectAreas = CARPETAS de los canvases que referencian la
    # tabla; diagrams = los canvases (sa3 en la raíz: diagrama sin subject area).
    # projects = el proyecto del reporte, en TODAS las filas (doc 75).
    assert by_id["t2"]["subjectAreas"] == ["1. Retail", "3. Riesgos"]
    assert by_id["t1"]["subjectAreas"] == ["1. Retail"]
    assert by_id["t3"]["subjectAreas"] == ["3. Riesgos"] and by_id["t3"]["diagrams"] == ["Risk", "Scratch"]
    assert by_id["t2"]["diagrams"] == ["Banking", "Risk"]
    assert all(r["projects"] == ["Core Banking"] for r in rows)

    # passthrough de campos de tabla
    assert by_id["t1"]["description"] == "Maestro de clientes"
    assert by_id["t1"]["schema"] == "core"


def test_table_rows_sorted_by_physical_name():
    rows = table_rows(*_fixture())
    assert [r["physicalName"] for r in rows] == ["CLIENTE", "CUENTA", "RIESGO"]


def test_table_rows_filter_by_schema():
    rows = table_rows(*_fixture(), filters={"schema": "core"})
    assert {r["id"] for r in rows} == {"t1", "t2"}


def test_table_rows_ignora_filtros_desconocidos():
    # Doc 75: el alcance por proyecto lo pone el repository; la agregación pura
    # sólo filtra por `schema`.
    rows = table_rows(*_fixture(), filters={"projectId": "p9"})
    assert {r["id"] for r in rows} == {"t1", "t2", "t3"}


def test_table_with_no_references_has_empty_lists():
    tables = [{"id": "t9", "physicalName": "ORPHAN", "logicalName": "Orphan"}]
    rows = table_rows(tables, {}, [], [], [])
    assert rows[0]["subjectAreas"] == [] and rows[0]["diagrams"] == []
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


def test_table_rows_metadata_completa_doc70():
    tables = [{"id": "t1", "physicalName": "HD_BASE", "logicalName": "base",
               "physicalNameOverridden": True, "logicalOnly": False, "physicalOnly": True},
              {"id": "t2", "physicalName": "PLAIN", "logicalName": "plain"}]
    rows = {r["id"]: r for r in table_rows(tables, {}, [], [], [])}
    assert (rows["t1"]["physicalNameOverridden"], rows["t1"]["logicalOnly"], rows["t1"]["physicalOnly"]) == (True, False, True)
    # defaults para docs previos a los docs 68/69
    assert (rows["t2"]["physicalNameOverridden"], rows["t2"]["logicalOnly"], rows["t2"]["physicalOnly"]) == (False, False, False)


def test_column_rows_metadata_completa_doc70():
    columns = [{"id": "c1", "tableId": "t1", "physicalName": "COD", "logicalName": "codigo",
                "dataType": "VARCHAR(30)", "parentDomainId": "d1", "ordinal": 3,
                "pkPosition": 0, "isPrimaryKey": True, "typeOverridden": True,
                "logicalTypeOverridden": False, "physicalNameOverridden": True, "logicalOnly": True},
               {"id": "c2", "tableId": "t1", "physicalName": "X", "logicalName": "x", "dataType": "INT", "ordinal": 4}]
    rows = {r["id"]: r for r in column_rows(columns, [{"id": "d1", "name": "Codigo"}])}
    c1 = rows["c1"]
    assert c1["parentDomain"] == "Codigo" and c1["parentDomainId"] == "d1"
    assert (c1["ordinal"], c1["pkPosition"]) == (3, 0)
    assert "logicalOrdinal" not in c1 and "columnOrdinal" not in c1   # doc 74: un solo orden
    assert (c1["typeOverridden"], c1["logicalTypeOverridden"], c1["physicalNameOverridden"]) == (True, False, True)
    assert (c1["logicalOnly"], c1["physicalOnly"]) == (True, False)
    c2 = rows["c2"]
    assert c2["parentDomainId"] is None and c2["pkPosition"] is None
    assert (c2["typeOverridden"], c2["physicalNameOverridden"], c2["logicalOnly"], c2["physicalOnly"]) == (False, False, False, False)


def test_column_rows_expone_physical_description_doc85():
    rows = column_rows([{"id": "c1", "tableId": "t1", "physicalName": "COD", "logicalName": "codigo",
                         "dataType": "INT", "ordinal": 0, "description": "Def", "physicalDescription": "Comentario"},
                        {"id": "c2", "tableId": "t1", "physicalName": "X", "logicalName": "x", "dataType": "INT", "ordinal": 1}], [])
    by_id = {r["id"]: r for r in rows}
    assert (by_id["c1"]["description"], by_id["c1"]["physicalDescription"]) == ("Def", "Comentario")
    assert by_id["c2"]["physicalDescription"] is None


# ── Orquestación por proyecto (doc 75) ─────────────────────────────────────


def _wire_repo(monkeypatch):
    calls: dict = {}

    async def _inputs(project_id):
        calls["inputs"] = project_id
        return {"tables": [{"id": "t1", "physicalName": "A", "schema": "core"}],
                "columnCounts": {}, "relationships": [], "subjectAreas": []}

    async def _page(project_id, limit, offset=0):
        calls["page"] = (project_id, limit) if not offset else (project_id, limit, offset)
        return {"tables": [{"id": "t1", "physicalName": "A", "schema": "core"}],
                "columnCounts": {}, "relationships": [], "subjectAreas": []}

    monkeypatch.setattr(service.repository, "report_inputs", _inputs)
    monkeypatch.setattr(service.repository, "report_inputs_page", _page)
    monkeypatch.setattr(service.repository, "udp_definitions", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.projects_repo, "get_project",
                        AsyncMock(return_value={"id": "p1", "name": "Core Banking"}))
    return calls


def test_list_table_rows_barre_el_proyecto_con_filtros(monkeypatch):
    calls = _wire_repo(monkeypatch)
    rows = asyncio.run(service.list_table_rows("p1", {"schema": "core"}, None))
    assert calls == {"inputs": "p1"}
    assert rows[0]["projects"] == ["Core Banking"]
    service.repository.udp_definitions.assert_awaited_once_with("p1")


def test_list_table_rows_fast_path_sin_filtros(monkeypatch):
    calls = _wire_repo(monkeypatch)
    rows = asyncio.run(service.list_table_rows("p1", {}, 50))
    assert calls == {"page": ("p1", 50)}
    assert rows[0]["projects"] == ["Core Banking"]
