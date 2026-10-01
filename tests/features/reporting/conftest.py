"""Fixtures compartidas de los tests del Reporting.

`report_version_db` (doc 102): el proyecto `P` en producción + el draft `cs1`
de ana, que borra, renombra, agrega y revive tablas, columnas, relaciones,
carpetas, canvases y vistas. Nombre propio (doc 105, A8): varios módulos de
esta carpeta definen su propio `db`, y antes esta fixture se importaba desde
otro módulo de tests."""
from __future__ import annotations

import pytest

from app.core.db import client as db_client
from tests.support.fakedb import FakeDb

from .helpers import P, ch


@pytest.fixture
def report_version_db(monkeypatch) -> FakeDb:
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
        ch("canonical_tables", "t2", "upsert", {"physicalName": "CUENTA_NUEVA", "logicalName": "Cuenta", "schema": "core"}),
        ch("canonical_tables", "t3", "delete"),
        ch("canonical_tables", "t4", "upsert", {"physicalName": "NUEVA", "logicalName": "Nueva", "schema": "nuevo"}),
        ch("canonical_tables", "t5", "upsert", {"physicalName": "NUEVA_DOS", "logicalName": "Nueva Dos", "schema": "nuevo"}),
        ch("canonical_columns", "c2", "delete"),
        ch("canonical_columns", "c4", "upsert", {"tableId": "t4", "physicalName": "ID", "logicalName": "Id", "dataType": "INT", "ordinal": 0}),
        ch("canonical_columns", "c9", "upsert", {"tableId": "t2", "physicalName": "VIEJA", "logicalName": "Vieja", "dataType": "INT", "ordinal": 1}),
        ch("relationships", "r2", "upsert", {"parentTableId": "t4", "childTableId": "t1",
                                              "pairs": [{"parentColumnId": "c4", "childColumnId": "c1"}]}),
        ch("folders", "f-new", "upsert", {"name": "Nuevo Subject", "parentFolderId": "f-root"}),
        ch("subject_areas", "sa2", "upsert", {"name": "Canvas B", "folderId": "f-new", "tableIds": ["t4"], "viewIds": ["v2"]}),
        ch("views", "v2", "upsert", {"name": "NUEVA", "schema": "nuevo_vu", "sourceTableIds": ["t4"],
                                     "sources": [{"tableId": "t4", "column": "ID"}]}),
    ])
    return fake
