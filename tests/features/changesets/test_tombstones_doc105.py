"""Doc 105, ronda 3 (revisor R4) — lápidas de `delete_changeset`.

Los retiros de la lápida no están condicionados a QUIÉN la escribió: una
eliminación NEGADA (doble clic / dueño y admin a la vez: la cabecera ya no
estaba) y la purga de arranque que encuentra la cabecera viva retiran la
lápida de la eliminación que SÍ está en curso. Si a esa eliminación le falla
después el borrado de los cambios (el caso H2: timeout con un ledger grande),
los huérfanos quedan sin lápida y ninguna purga los vuelve a encontrar."""
from __future__ import annotations

import asyncio

import pytest

from app.core.db import client as db_client
from app.features.changesets import repository
from app.features.changesets.service import DELETABLE
from tests.support.fakedb import FakeCollection, FakeDb


@pytest.fixture
def db(monkeypatch) -> FakeDb:
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    fake.raw["changesets"].insert_one({"_id": "cs1", "title": "t", "owner": "ana", "projectId": "p",
                                       "status": "draft"})
    fake.raw["changeset_changes"].insert_many([
        {"_id": f"cs1::canonical_tables::t{i}", "csId": "cs1", "collection": "canonical_tables",
         "entityId": f"t{i}", "op": "upsert", "payload": {}, "at": "T0"} for i in range(3)])
    return fake


def _later(monkeypatch, seconds):
    now = repository._clock()
    monkeypatch.setattr(repository, "_clock", lambda: now + seconds)


def test_una_eliminacion_negada_retira_la_lapida_de_la_que_esta_en_curso(db, monkeypatch):
    real = FakeCollection.delete_many

    async def interleaved(self, flt, *a, **kw):
        if self.name == repository.CHANGES_COLL:
            # A ya borró la cabecera y va por los cambios; B (doble clic, leyó la
            # cabecera antes) llega a su delete: se niega y retira «su» lápida.
            assert await repository.delete_changeset("cs1", "ana", DELETABLE) is None
            raise TimeoutError("timeout borrando 300k cambios")
        return await real(self, flt, *a, **kw)

    monkeypatch.setattr(FakeCollection, "delete_many", interleaved)
    assert asyncio.run(repository.delete_changeset("cs1", "ana", DELETABLE)) == 3   # «eliminada», cambios para la purga
    monkeypatch.setattr(FakeCollection, "delete_many", real)
    assert db.raw["changesets"].find_one({"_id": "cs1"}) is None
    _later(monkeypatch, repository.TOMBSTONE_GRACE_SECONDS + 60)
    asyncio.run(repository.purge_orphan_changes())
    assert db.raw["changeset_changes"].count_documents({"csId": "cs1"}) == 0, \
        "huérfanos sin lápida: la purga nunca los encuentra"


def test_la_purga_retira_la_lapida_nueva_de_una_eliminacion_que_arranca(db, monkeypatch):
    """Lápida vieja (un intento anterior que murió antes de borrar la cabecera).
    La purga del arranque ve la cabecera VIVA y retira la lápida — pero en el
    medio el dueño volvió a eliminar (lápida nueva, reemplazada por `_id`)."""
    db.raw[repository.DELETED_COLL].insert_one({"_id": "cs1", "at": repository._clock() - 3600})
    real_find_one = FakeCollection.find_one
    real_delete = FakeCollection.delete_many
    state = {"hooked": False, "fail": False}

    async def find_one(self, flt=None, *a, **kw):
        doc = await real_find_one(self, flt, *a, **kw)
        if self.name == repository.COLL and not state["hooked"] and (flt or {}).get("_id") == "cs1":
            state["hooked"] = True
            # Justo después de que la purga vio la cabecera viva: eliminación nueva
            # que borra la cabecera y a la que le falla el borrado de los cambios.
            state["fail"] = True
            await repository.delete_changeset("cs1", "ana", DELETABLE)
        return doc

    async def delete_many(self, flt, *a, **kw):
        if self.name == repository.CHANGES_COLL and state["fail"]:
            state["fail"] = False
            raise TimeoutError("timeout borrando los cambios")
        return await real_delete(self, flt, *a, **kw)

    monkeypatch.setattr(FakeCollection, "find_one", find_one)
    monkeypatch.setattr(FakeCollection, "delete_many", delete_many)
    asyncio.run(repository.purge_orphan_changes())                   # retira la lápida NUEVA
    monkeypatch.setattr(FakeCollection, "find_one", real_find_one)
    monkeypatch.setattr(FakeCollection, "delete_many", real_delete)
    assert db.raw["changesets"].find_one({"_id": "cs1"}) is None
    _later(monkeypatch, repository.TOMBSTONE_GRACE_SECONDS + 60)
    asyncio.run(repository.purge_orphan_changes())                   # el arranque siguiente
    assert db.raw["changeset_changes"].count_documents({"csId": "cs1"}) == 0, \
        "huérfanos sin lápida: la purga nunca los encuentra"


def test_tras_una_caida_la_purga_del_reinicio_inmediato_ya_limpia(db, monkeypatch):
    """Nota del revisor: con la gracia de 10 min para TODA lápida, el reinicio
    inmediato después de una caída nunca limpiaba (recién un arranque
    posterior). Sin cabecera no hay nada en curso que proteger: se purga ya."""
    real = FakeCollection.delete_many

    async def flaky(self, flt, *a, **kw):
        if self.name == repository.CHANGES_COLL:
            raise TimeoutError("timeout")
        return await real(self, flt, *a, **kw)

    monkeypatch.setattr(FakeCollection, "delete_many", flaky)
    asyncio.run(repository.delete_changeset("cs1", "ana", DELETABLE))
    monkeypatch.setattr(FakeCollection, "delete_many", real)
    assert asyncio.run(repository.purge_orphan_changes()) == 3               # sin esperar la gracia
    assert db.raw[repository.DELETED_COLL].count_documents({}) == 0


def test_una_lapida_reciente_de_una_version_viva_se_respeta(db):
    """Cabecera viva + lápida reciente = una eliminación en curso en el otro
    proceso (aún no borró la cabecera): ni sus cambios ni su lápida se tocan."""
    db.raw[repository.DELETED_COLL].insert_one({"_id": "cs1:x", "csId": "cs1", "at": repository._clock()})
    assert asyncio.run(repository.purge_orphan_changes()) == 0
    assert db.raw[repository.DELETED_COLL].count_documents({}) == 1
    assert db.raw["changeset_changes"].count_documents({"csId": "cs1"}) == 3
