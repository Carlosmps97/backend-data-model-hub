"""Doc 105 (H1 y restore): `set_changes_bulk` graba en tandas de 1000. Si una
tanda falla con una excepción (timeout), las anteriores de ESA llamada se
deshacen antes de relanzar: un rename de esquema o un restore de más de 1000
entidades no queda a medias (salvo que la base se caiga también para la
compensación). Lo previo del draft se restaura tal cual."""
from __future__ import annotations

import asyncio

import pytest

from app.core.db import client as db_client
from app.features.changesets import repository
from tests.support.fakedb import FakeCollection, FakeDb


@pytest.fixture
def db(monkeypatch) -> FakeDb:
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    fake.raw["changesets"].insert_one({"_id": "cs1", "title": "t", "owner": "ana", "projectId": "p",
                                       "status": "draft"})
    return fake


def test_si_una_tanda_falla_se_deshacen_las_anteriores(db, monkeypatch):
    prev_key = repository.change_key("cs1", "canonical_tables", "t0")
    db.raw["changeset_changes"].insert_one({"_id": prev_key, "csId": "cs1", "collection": "canonical_tables",
                                            "entityId": "t0", "op": "upsert", "payload": {"schema": "OLD"},
                                            "at": "T0", "wtoken": "antes"})
    items = [{"collection": "canonical_tables", "entityId": f"t{i}", "op": "upsert", "payload": {"schema": "NEW"}}
             for i in range(2001)]
    real = FakeCollection.bulk_write
    calls = {"n": 0}

    async def flaky(self, ops, ordered=True):
        if self.name == "changeset_changes":
            calls["n"] += 1
            if calls["n"] == 2:
                raise TimeoutError("timeout en la segunda tanda")
        return await real(self, ops, ordered)

    monkeypatch.setattr(FakeCollection, "bulk_write", flaky)
    with pytest.raises(TimeoutError):
        asyncio.run(repository.set_changes_bulk("cs1", items, owner="ana"))
    monkeypatch.setattr(FakeCollection, "bulk_write", real)
    docs = list(db.raw["changeset_changes"].find({"csId": "cs1"}))
    assert [d["_id"] for d in docs] == [prev_key]                    # nada nuevo quedó…
    assert docs[0]["payload"] == {"schema": "OLD"}                    # …y lo previo volvió tal cual


def test_una_cancelacion_entre_tandas_tambien_se_deshace(db, monkeypatch):
    """Revisión R1: la compensación sólo corría ante `Exception`; una
    cancelación (`CancelledError`, p. ej. al apagar el proceso) entre tandas
    dejaba las anteriores grabadas."""
    items = [{"collection": "canonical_tables", "entityId": f"t{i}", "op": "upsert", "payload": {"schema": "NEW"}}
             for i in range(2001)]
    real = FakeCollection.bulk_write
    calls = {"n": 0}

    async def cancelled(self, ops, ordered=True):
        if self.name == "changeset_changes":
            calls["n"] += 1
            if calls["n"] == 2:
                raise asyncio.CancelledError()
        return await real(self, ops, ordered)

    monkeypatch.setattr(FakeCollection, "bulk_write", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(repository.set_changes_bulk("cs1", items, owner="ana"))
    monkeypatch.setattr(FakeCollection, "bulk_write", real)
    assert db.raw["changeset_changes"].count_documents({"csId": "cs1"}) == 0


def test_conservar_marcas_previas_escribe_por_id_y_no_pisa_las_que_ya_estan(db, monkeypatch):
    """Revisión R1/R2: con `keep_existing` (re-aprobar tras un publish a medias)
    el filtro llevaba `beforeAt: {$exists: false}` y el adaptador no tomaba su
    camino rápido (en Lakebase, una sentencia por entidad). Ahora se leen
    primero las marcas que ya están y se escribe por `_id` sólo lo que falta."""
    k1 = repository.change_key("cs1", "canonical_tables", "t1")
    k2 = repository.change_key("cs1", "canonical_tables", "t2")
    db.raw["changeset_changes"].insert_many([
        {"_id": k1, "csId": "cs1", "collection": "canonical_tables", "entityId": "t1", "op": "upsert",
         "payload": {}, "at": "T0", "before": {"id": "t1", "v": "limpia"}, "beforeAt": "B0"},
        {"_id": k2, "csId": "cs1", "collection": "canonical_tables", "entityId": "t2", "op": "upsert",
         "payload": {}, "at": "T0"},
    ])
    ops: list = []
    real = FakeCollection.bulk_write

    async def spy(self, batch, ordered=True):
        if self.name == "changeset_changes":
            ops.extend(batch)
        return await real(self, batch, ordered)

    monkeypatch.setattr(FakeCollection, "bulk_write", spy)
    asyncio.run(repository.store_before_images(
        "cs1", {("canonical_tables", "t1"): {"id": "t1", "v": "contaminada"}, ("canonical_tables", "t2"): None},
        keep_existing=True))
    assert ops and all(list(op._filter) == ["_id"] for op in ops)          # camino rápido del adaptador
    d1 = db.raw["changeset_changes"].find_one({"_id": k1})
    d2 = db.raw["changeset_changes"].find_one({"_id": k2})
    assert d1["before"] == {"id": "t1", "v": "limpia"} and d1["beforeAt"] == "B0"   # la marca previa se conserva
    assert d2["beforeAt"] and d2["before"] is None                                   # lo que faltaba, se estampa
