"""Doc 102: el Reporting sobre una versión propia — tablas, conteos, carpetas,
filtros, columnas y vistas con la versión (borra, renombra, agrega, revive)."""
from __future__ import annotations

import asyncio

import pytest

from app.core.db import client as db_client
from app.features.changesets import repository as cs_repo
from app.features.reporting import service, versions
from tests.support.fakedb import FakeDb

P = "p1"
AT = "2026-09-29T00:00:00+00:00"


def _ch(coll, eid, op, payload=None):
    doc = {"_id": f"cs1::{coll}::{eid}", "csId": "cs1", "collection": coll, "entityId": eid, "op": op, "at": AT}
    if payload is not None:
        doc["payload"] = {"projectId": P, **payload}
    return doc


@pytest.fixture
def db(monkeypatch) -> FakeDb:
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    raw = fake.raw
    on = {"projectId": P, "flgactive": True}
    raw["projects"].insert_one({"_id": P, "name": "MODELO DDV", "flgactive": True})
    raw["folders"].insert_many([
        {"_id": "f-root", **on, "name": "CPYBCA", "parentFolderId": None},
        {"_id": "f-sub", **on, "name": "Despriorizado", "parentFolderId": "f-root"}])
    raw["subject_areas"].insert_one({"_id": "sa1", **on, "name": "Canvas A", "folderId": "f-sub",
                                     "tableIds": ["t1", "t2"], "viewIds": ["v1"]})
    raw["canonical_tables"].insert_many([
        {"_id": "t1", **on, "physicalName": "CLIENTE", "logicalName": "Cliente", "schema": "core"},
        {"_id": "t2", **on, "physicalName": "CUENTA", "logicalName": "Cuenta", "schema": "core"},
        {"_id": "t3", **on, "physicalName": "RIESGO", "logicalName": "Riesgo", "schema": "risk"}])
    col = lambda cid, tid, name, ordinal, active=True: {  # noqa: E731
        "_id": cid, "projectId": P, "flgactive": active, "tableId": tid, "physicalName": name,
        "logicalName": name.title(), "dataType": "INT", "ordinal": ordinal}
    raw["canonical_columns"].insert_many([
        col("c1", "t1", "COD", 0), col("c2", "t1", "NOM", 1), col("c3", "t2", "CTA", 0),
        col("c9", "t2", "VIEJA", 1, active=False)])
    raw["relationships"].insert_one({"_id": "r1", **on, "parentTableId": "t1", "childTableId": "t2",
                                     "pairs": [{"parentColumnId": "c1", "childColumnId": "c3"}]})
    raw["views"].insert_one({"_id": "v1", **on, "name": "CLIENTE", "schema": "core_vu",
                             "sourceTableIds": ["t1"], "sources": [{"tableId": "t1", "column": "COD"}]})
    raw["changesets"].insert_one({"_id": "cs1", "title": "v2", "owner": "ana", "projectId": P, "status": "draft"})
    raw["changeset_changes"].insert_many([
        _ch("canonical_tables", "t2", "upsert", {"physicalName": "CUENTA_NUEVA", "logicalName": "Cuenta", "schema": "core"}),
        _ch("canonical_tables", "t3", "delete"),
        _ch("canonical_tables", "t4", "upsert", {"physicalName": "NUEVA", "logicalName": "Nueva", "schema": "nuevo"}),
        _ch("canonical_tables", "t5", "upsert", {"physicalName": "NUEVA_DOS", "logicalName": "Nueva Dos", "schema": "nuevo"}),
        _ch("canonical_columns", "c2", "delete"),
        _ch("canonical_columns", "c4", "upsert", {"tableId": "t4", "physicalName": "ID", "logicalName": "Id", "dataType": "INT", "ordinal": 0}),
        _ch("canonical_columns", "c9", "upsert", {"tableId": "t2", "physicalName": "VIEJA", "logicalName": "Vieja", "dataType": "INT", "ordinal": 1}),
        _ch("relationships", "r2", "upsert", {"parentTableId": "t4", "childTableId": "t1",
                                               "pairs": [{"parentColumnId": "c4", "childColumnId": "c1"}]}),
        _ch("folders", "f-new", "upsert", {"name": "Nuevo Subject", "parentFolderId": "f-root"}),
        _ch("subject_areas", "sa2", "upsert", {"name": "Canvas B", "folderId": "f-new", "tableIds": ["t4"], "viewIds": ["v2"]}),
        _ch("views", "v2", "upsert", {"name": "NUEVA", "schema": "nuevo_vu", "sourceTableIds": ["t4"],
                                      "sources": [{"tableId": "t4", "column": "ID"}]}),
    ])
    return fake


def _changes(collections):
    return asyncio.run(cs_repo.changes_map("cs1", collections))


def test_produccion_no_cambia(db):
    rows = asyncio.run(service.list_table_rows(P, {}, None))
    assert [r["physicalName"] for r in rows] == ["CLIENTE", "CUENTA", "RIESGO"]
    assert asyncio.run(service.count_tables(P)) == 3


def test_filas_de_la_version_con_conteos_carpetas_y_diagramas(db):
    ch = _changes(versions.TABLE_INPUTS)
    rows = {r["id"]: r for r in asyncio.run(service.list_table_rows(P, {}, None, changes=ch))}
    assert sorted(rows) == ["t1", "t2", "t4", "t5"]                       # t3 borrada; t4/t5 nuevas
    assert rows["t2"]["physicalName"] == "CUENTA_NUEVA"
    assert (rows["t1"]["columnCount"], rows["t2"]["columnCount"], rows["t4"]["columnCount"]) == (1, 2, 1)
    assert (rows["t1"]["relationshipCount"], rows["t2"]["relationshipCount"], rows["t4"]["relationshipCount"]) == (2, 1, 1)
    t4 = rows["t4"]
    assert (t4["subjectAreas"], t4["diagrams"], t4["spaces"]) == (["Nuevo Subject"], ["Canvas B"], ["CPYBCA"])


def test_pagina_de_la_version_sale_del_orden_completo(db):
    page = asyncio.run(service.list_table_rows(P, {}, 2, 1, changes=_changes(versions.TABLE_INPUTS)))
    assert [r["physicalName"] for r in page] == ["CUENTA_NUEVA", "NUEVA"]


def test_conteo_y_filtros_de_la_version(db):
    assert asyncio.run(service.count_tables(P, _changes(versions.COUNT_INPUTS))) == 4
    f = asyncio.run(service.filter_options(P, _changes(versions.FILTER_INPUTS)))
    assert f == {"schemas": ["core", "nuevo"], "subjectAreas": ["Despriorizado", "Nuevo Subject"]}
    assert asyncio.run(service.filter_options(P)) == {"schemas": ["core", "risk"], "subjectAreas": ["Despriorizado"]}


def test_columnas_del_lote_con_la_version(db):
    got = {r["id"] for r in asyncio.run(service.list_column_rows(P, None, ["t1", "t2", "t4"], changeset_id="cs1"))}
    assert got == {"c1", "c3", "c9", "c4"}                                 # c2 baja; c9 revive; c4 alta
    assert [c["id"] for c in asyncio.run(service.list_column_rows(P, None, ["t1"], changeset_id="cs1"))] == ["c1"]


def test_vistas_del_lote_con_la_version(db):
    ch = _changes(versions.VIEW_INPUTS)
    (v2,) = asyncio.run(service.list_view_rows(P, ["t4"], changes=ch))
    assert (v2["id"], v2["sourceTables"], v2["canvases"]) == ("v2", ["nuevo.NUEVA"], ["Canvas B"])
    assert [v["id"] for v in asyncio.run(service.list_view_rows(P, ["t1"], changes=ch))] == ["v1"]


def test_relaciones_de_la_version_con_nombres_efectivos(db):
    from app.features.reporting import views
    ch = _changes(versions.RELATIONSHIP_INPUTS)
    got = sorted((r["id"], r["parent"], r["child"]) for r in asyncio.run(views.relationships_report(P, changes=ch)))
    assert got == [("r1", "CLIENTE.COD", "CUENTA_NUEVA.CTA"), ("r2", "NUEVA.ID", "CLIENTE.COD")]
    prod = asyncio.run(views.relationships_report(P))
    assert [(r["id"], r["child"]) for r in prod] == [("r1", "CUENTA.CTA")]
