"""`repository.earliest_applied` devuelve la versión APLICADA más vieja DEL
PROYECTO (doc 51 / doc 75) — contra una BD falsa REAL, no un mock.

Regresión (doc 82): el doc 75 partió esta función en dos al insertar
`changesets_deleting_project` en el medio; quedó sin `return` y el historial
perdió la fila «Initial load». El test de `entity_history` la mockeaba, así
que la suite siguió en verde."""
from __future__ import annotations

import asyncio

import pytest

from app.core.db import client as db_client
from app.features.changesets import repository
from tests.support.fakedb import FakeDb


@pytest.fixture
def db(monkeypatch) -> FakeDb:
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    return fake


def _cs(db: FakeDb, cs_id: str, project: str, applied: str | None, status: str = "approved", **extra) -> None:
    db.raw["changesets"].insert_one({"_id": cs_id, "projectId": project, "status": status,
                                     "appliedAt": applied, "versionLabel": extra.get("label", "v1"),
                                     "title": extra.get("title", cs_id), "owner": "x"})


def test_earliest_applied_devuelve_la_v1_del_proyecto(db):
    _cs(db, "a-v2", "A", "2026-09-02T00:00:00+00:00", label="v2")
    _cs(db, "a-v1", "A", "2026-09-01T00:00:00+00:00", label="v1", title="Base")
    _cs(db, "b-v1", "B", "2026-08-01T00:00:00+00:00", label="v1")          # otro proyecto, más vieja
    _cs(db, "a-draft", "A", None, status="draft", label="v3")

    out = asyncio.run(repository.earliest_applied("A"))

    assert out == {"id": "a-v1", "appliedAt": "2026-09-01T00:00:00+00:00", "versionLabel": "v1", "title": "Base"}


def test_earliest_applied_sin_versiones_aplicadas_es_none(db):
    _cs(db, "a-draft", "A", None, status="draft")
    assert asyncio.run(repository.earliest_applied("A")) is None


def test_changesets_deleting_project_sigue_intacta(db):
    db.raw["changeset_changes"].insert_one({"_id": "cs1::projects::P", "csId": "cs1", "collection": "projects",
                                            "entityId": "P", "op": "delete"})
    db.raw["changeset_changes"].insert_one({"_id": "cs2::canonical_tables::t", "csId": "cs2",
                                            "collection": "canonical_tables", "entityId": "t", "op": "upsert"})
    assert asyncio.run(repository.changesets_deleting_project(["cs1", "cs2", "cs3"])) == {"cs1"}
    assert asyncio.run(repository.changesets_deleting_project([])) == set()
