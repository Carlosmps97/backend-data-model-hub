"""Doc 102: el Reporting sobre una versión propia — tablas, conteos, carpetas,
filtros, columnas y vistas con la versión (borra, renombra, agrega, revive)."""
from __future__ import annotations

import asyncio

from app.features.changesets import repository as cs_repo
from app.features.reporting import service, versions

from .helpers import P


def _changes(collections):
    return asyncio.run(cs_repo.changes_map("cs1", collections))


def test_produccion_no_cambia(report_version_db):
    rows = asyncio.run(service.list_table_rows(P, {}, None))
    assert [r["physicalName"] for r in rows] == ["CLIENTE", "CUENTA", "RIESGO"]
    assert asyncio.run(service.count_tables(P)) == 3


def test_filas_de_la_version_con_conteos_carpetas_y_diagramas(report_version_db):
    ch = asyncio.run(versions.table_changes_of("cs1"))          # doc 105 (A7): columnas proyectadas
    rows = {r["id"]: r for r in asyncio.run(service.list_table_rows(P, {}, None, changes=ch))}
    assert sorted(rows) == ["t1", "t2", "t4", "t5"]                       # t3 borrada; t4/t5 nuevas
    assert rows["t2"]["physicalName"] == "CUENTA_NUEVA"
    assert (rows["t1"]["columnCount"], rows["t2"]["columnCount"], rows["t4"]["columnCount"]) == (1, 2, 1)
    assert (rows["t1"]["relationshipCount"], rows["t2"]["relationshipCount"], rows["t4"]["relationshipCount"]) == (2, 1, 1)
    t4 = rows["t4"]
    assert (t4["subjectAreas"], t4["diagrams"], t4["spaces"]) == (["Nuevo Subject"], ["Canvas B"], ["CPYBCA"])


def test_pagina_de_la_version_sale_del_orden_completo(report_version_db):
    page = asyncio.run(service.list_table_rows(P, {}, 2, 1, changes=asyncio.run(versions.table_changes_of("cs1"))))
    assert [r["physicalName"] for r in page] == ["CUENTA_NUEVA", "NUEVA"]


def test_conteo_y_filtros_de_la_version(report_version_db):
    assert asyncio.run(service.count_tables(P, _changes(versions.COUNT_INPUTS))) == 4
    f = asyncio.run(service.filter_options(P, _changes(versions.FILTER_INPUTS)))
    assert f == {"schemas": ["core", "nuevo"], "subjectAreas": ["Despriorizado", "Nuevo Subject"]}
    assert asyncio.run(service.filter_options(P)) == {"schemas": ["core", "risk"], "subjectAreas": ["Despriorizado"]}


def test_columnas_del_lote_con_la_version(report_version_db):
    got = {r["id"] for r in asyncio.run(service.list_column_rows(P, None, ["t1", "t2", "t4"], changeset_id="cs1"))}
    assert got == {"c1", "c3", "c9", "c4"}                                 # c2 baja; c9 revive; c4 alta
    assert [c["id"] for c in asyncio.run(service.list_column_rows(P, None, ["t1"], changeset_id="cs1"))] == ["c1"]


def test_vistas_del_lote_con_la_version(report_version_db):
    ch = _changes(versions.VIEW_INPUTS)
    (v2,) = asyncio.run(service.list_view_rows(P, ["t4"], changes=ch))
    assert (v2["id"], v2["sourceTables"], v2["canvases"]) == ("v2", ["nuevo.NUEVA"], ["Canvas B"])
    assert [v["id"] for v in asyncio.run(service.list_view_rows(P, ["t1"], changes=ch))] == ["v1"]


def test_relaciones_de_la_version_con_nombres_efectivos(report_version_db):
    from app.features.reporting import views
    ch = _changes(versions.RELATIONSHIP_INPUTS)
    got = sorted((r["id"], r["parent"], r["child"]) for r in asyncio.run(views.relationships_report(P, changes=ch)))
    assert got == [("r1", "CLIENTE.COD", "CUENTA_NUEVA.CTA"), ("r2", "NUEVA.ID", "CLIENTE.COD")]
    prod = asyncio.run(views.relationships_report(P))
    assert [(r["id"], r["child"]) for r in prod] == [("r1", "CUENTA.CTA")]
