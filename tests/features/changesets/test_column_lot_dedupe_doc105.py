"""Doc 105 (R2-A3): una edición EN SITIO de una columna llega por las dos
lecturas del lote (por `_id` y por `payload.tableId`); se valida una sola vez."""
from __future__ import annotations

import asyncio

import pytest

from app.core.db import client as db_client
from app.features.changesets import repository as cs_repo
from tests.support.fakedb import FakeDb


@pytest.fixture
def fake(monkeypatch) -> FakeDb:
    db = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", db)
    return db


def test_cada_cambio_del_lote_se_valida_una_vez(fake, monkeypatch):
    fake.raw["changeset_changes"].insert_one({
        "_id": "cs1::canonical_columns::c1", "csId": "cs1", "collection": "canonical_columns",
        "entityId": "c1", "op": "upsert", "at": "2026-01-01T00:00:00+00:00",
        "payload": {"tableId": "t1", "physicalName": "C1", "ordinal": 0}})
    seen: list[str] = []
    real = cs_repo._ledger_entry
    monkeypatch.setattr(cs_repo, "_ledger_entry", lambda d: (seen.append(d["_id"]), real(d))[1])
    out = asyncio.run(cs_repo.column_changes_for_tables("cs1", ["c1"], ["t1"]))
    assert list(out) == ["c1"]
    assert seen == ["cs1::canonical_columns::c1"]
