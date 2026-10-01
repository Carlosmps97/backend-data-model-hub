"""Doc 105 (H2): eliminar un draft borra la cabecera y DESPUÉS sus cambios (en
ese orden a propósito, doc 104). Si lo segundo falla o el proceso muere en el
medio, quedan cambios huérfanos (inofensivos: ningún lector los ve, pero
ocupan). La eliminación igual responde bien y `purge_orphan_changes` — que
corre al arrancar la app — los limpia."""
from __future__ import annotations

import asyncio

from app.features.changesets import repository
from tests.support.fakedb import FakeCollection


def _draft(api, world):
    cs = api("ana").post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    api("ana").change(cs, "canonical_tables", "t-orphan", {"physicalName": "M_ORPHAN", "logicalName": "Orphan",
                                                          "schema": "STG"})
    return cs


def test_si_falla_borrar_los_cambios_la_version_igual_queda_eliminada(api, world, fake_db, monkeypatch):
    cs = _draft(api, world)
    real = FakeCollection.delete_many

    async def flaky(self, flt):
        if self.name == "changeset_changes":
            raise TimeoutError("timeout borrando los cambios")
        return await real(self, flt)

    monkeypatch.setattr(FakeCollection, "delete_many", flaky)
    out = api("ana").delete(f"/api/changesets/{cs}")
    assert out["deleted"] is True and out["changes"] == 1          # lo que tenía (quedan para la limpieza)
    monkeypatch.setattr(FakeCollection, "delete_many", real)
    assert fake_db.raw["changesets"].find_one({"_id": cs}) is None
    assert fake_db.raw["changeset_changes"].count_documents({"csId": cs}) == 1


def _delete_leaving_orphans(api, world, monkeypatch):
    cs = _draft(api, world)
    real = FakeCollection.delete_many

    async def flaky(self, flt):
        if self.name == "changeset_changes":
            raise TimeoutError("timeout borrando los cambios")
        return await real(self, flt)

    monkeypatch.setattr(FakeCollection, "delete_many", flaky)
    api("ana").delete(f"/api/changesets/{cs}")
    monkeypatch.setattr(FakeCollection, "delete_many", real)
    return cs


def _later(monkeypatch, seconds):
    now = repository._clock()
    monkeypatch.setattr(repository, "_clock", lambda: now + seconds)


def test_purge_borra_solo_los_huerfanos(api, world, fake_db, monkeypatch):
    alive = _draft(api, world)
    gone = _delete_leaving_orphans(api, world, monkeypatch)
    assert fake_db.raw["changeset_changes"].count_documents({"csId": gone}) == 1
    _later(monkeypatch, repository.TOMBSTONE_GRACE_SECONDS + 1)
    assert asyncio.run(repository.purge_orphan_changes()) == 1
    assert fake_db.raw["changeset_changes"].count_documents({"csId": gone}) == 0
    assert fake_db.raw["changeset_changes"].count_documents({"csId": alive}) == 1
    assert fake_db.raw[repository.DELETED_COLL].count_documents({}) == 0
    assert asyncio.run(repository.purge_orphan_changes()) == 0     # idempotente


def test_purge_no_recorre_el_ledger_entero(api, world, fake_db, monkeypatch):
    """Revisión R1: la purga hacía `distinct("csId")` sobre TODO el ledger (en
    Lakebase, un escaneo completo con jsonpath) en cada arranque de cada
    worker. Ahora sigue las lápidas que deja la eliminación."""
    gone = _delete_leaving_orphans(api, world, monkeypatch)
    _later(monkeypatch, repository.TOMBSTONE_GRACE_SECONDS + 1)
    real = FakeCollection.distinct
    scans: list[str] = []

    async def spy(self, key, filter=None):
        scans.append(self.name)
        return await real(self, key, filter)

    monkeypatch.setattr(FakeCollection, "distinct", spy)
    assert asyncio.run(repository.purge_orphan_changes()) == 1
    assert "changeset_changes" not in scans
    assert fake_db.raw["changeset_changes"].count_documents({"csId": gone}) == 0


def test_una_eliminacion_normal_no_deja_lapida(api, world, fake_db):
    cs = _draft(api, world)
    assert api("ana").delete(f"/api/changesets/{cs}")["deleted"] is True
    assert fake_db.raw[repository.DELETED_COLL].count_documents({}) == 0


def test_una_eliminacion_rechazada_no_deja_lapida(api, world, fake_db):
    cs = _draft(api, world)
    api("ana").post(f"/api/changesets/{cs}/submit", {"title": "t", "reviewers": ["beto"]})
    st, _ = api("ana").call("DELETE", f"/api/changesets/{cs}")
    assert st == 409
    assert fake_db.raw[repository.DELETED_COLL].count_documents({}) == 0


def test_lapida_de_un_proceso_que_murio_antes_de_borrar_la_cabecera(api, world, fake_db, monkeypatch):
    """La lápida va ANTES que la cabecera: si el proceso muere en el medio, la
    versión sigue viva — la purga sólo retira la lápida y no toca sus cambios."""
    cs = _draft(api, world)
    fake_db.raw[repository.DELETED_COLL].insert_one({"_id": cs, "at": repository._clock()})
    assert asyncio.run(repository.purge_orphan_changes()) == 0            # reciente: puede estar en curso
    assert fake_db.raw[repository.DELETED_COLL].count_documents({}) == 1
    _later(monkeypatch, repository.TOMBSTONE_GRACE_SECONDS + 1)
    assert asyncio.run(repository.purge_orphan_changes()) == 0
    assert fake_db.raw[repository.DELETED_COLL].count_documents({}) == 0
    assert fake_db.raw["changeset_changes"].count_documents({"csId": cs}) == 1
