"""Doc 110 — puente sync de los scripts con el escritor resistente.

- `bulk_write` idempotente por `_id` pasa por el escritor (tiempo máximo y
  reintento en otra conexión). Por defecto espera el resultado (scripts de
  siempre); con `pipeline()` (migrate) encola y vuelve.
- Toda lectura u otra escritura vacía antes la cola: se lee lo ya escrito y el
  orden de las escrituras se respeta.
- Una lectura que pierde la conexión se repite.
- Las conexiones de los scripts se abren PROBADAS (`connect(probe=True)`).

BD falsa: `FakeServer` aplica las sentencias del camino rápido; las lecturas
devuelven sus filas. Lo lento o caído son las conexiones del guion."""
from __future__ import annotations

import asyncio
import json
import time

import asyncpg
import pytest
from pymongo import DeleteOne

from app.core.db import sync as sync_mod
from app.core.db.lakebase.collection import PgCollection, _WriteResult
from app.core.db.sync import SyncDatabase
from tests.lakebase.test_writer_doc110 import FakeConn, FakePool, FakeServer, _up


class _ServerColl(PgCollection):
    def find(self, filter=None, projection=None):
        db = self.database

        class Cur:
            async def to_list(self, length=None):
                await asyncio.sleep(0)
                return [dict(r) for r in db.server.rows.values()]
        return Cur()

    async def find_one(self, filter=None, projection=None):
        if self.database.flaky_reads or time.monotonic() < self.database.down_until:
            self.database.flaky_reads = max(0, self.database.flaky_reads - 1)
            raise asyncpg.exceptions.ConnectionDoesNotExistError(
                "connection was closed in the middle of operation")
        return next((dict(r) for r in self.database.server.rows.values()), None)

    async def bulk_write(self, ops, ordered=True):          # camino general (no idempotente)
        for op in ops:
            self.database.server.rows.pop(op._filter["_id"], None)
        return _WriteResult(deleted=len(ops))


class FakeLakebaseDb:
    schema = "dmh"

    def __init__(self, server: FakeServer, conns: list[FakeConn]) -> None:
        self.server = server
        self.pool = FakePool(conns)
        self.flaky_reads = 0
        self.down_until = 0.0

    async def ensure_table(self, name):
        return None

    def __getitem__(self, name):
        return _ServerColl(self, name)


def _bridge(server, conns, *, pipelined=False) -> SyncDatabase:
    sdb = SyncDatabase()
    sdb._adb = FakeLakebaseDb(server, conns)
    if pipelined:
        sdb.pipeline(workers=1)
    return sdb


def test_en_cola_vuelve_enseguida_y_la_lectura_ve_lo_escrito():
    server = FakeServer()
    sdb = _bridge(server, [FakeConn(server, delay=0.05)], pipelined=True)
    assert sdb["c"].bulk_write([_up("a", 1)]) is None
    assert list(sdb["c"].find({})) == [{"_id": "a", "c": 1, "v": 1}]


def test_por_defecto_espera_y_devuelve_las_filas_que_ya_existian():
    server = FakeServer({"a": {"_id": "a", "v": 0}})
    sdb = _bridge(server, [FakeConn(server)])
    res = sdb["c"].bulk_write([_up("a", 1), _up("b", 1)])
    assert res.matched_count == 1
    assert server.rows == {"a": {"_id": "a", "v": 1}, "b": {"_id": "b", "c": 1, "v": 1}}


def test_por_defecto_no_se_queda_con_conexiones_tomadas():
    server = FakeServer()
    sdb = _bridge(server, [FakeConn(server)])
    sdb["c"].bulk_write([_up("a", 1)])
    pool = sdb._adb.pool
    assert pool.handed and pool.released == pool.handed


def test_un_lote_no_idempotente_espera_a_lo_encolado_antes():
    server = FakeServer()
    sdb = _bridge(server, [FakeConn(server, delay=0.05)], pipelined=True)
    sdb["c"].bulk_write([_up("a", 1)])
    sdb["c"].bulk_write([DeleteOne({"_id": "a"})])
    assert server.rows == {}


def test_drain_propaga_el_error_de_la_cola():
    server = FakeServer()
    sdb = _bridge(server, [FakeConn(server, fail=asyncpg.exceptions.DataError("bad json"))], pipelined=True)
    sdb["c"].bulk_write([_up("a", 1)])
    with pytest.raises(asyncpg.exceptions.DataError):
        sdb.drain()


def test_una_lectura_que_pierde_la_conexion_se_repite(monkeypatch):
    monkeypatch.setattr(sync_mod, "_READ_PAUSE", 0.01)
    server = FakeServer({"a": {"_id": "a", "v": 1}})
    sdb = _bridge(server, [])
    sdb._adb.flaky_reads = 1
    assert sdb["c"].find_one({}) == {"_id": "a", "v": 1}


def test_las_conexiones_de_los_scripts_se_abren_probadas(monkeypatch):
    seen: list[dict] = []
    fake = FakeLakebaseDb(FakeServer(), [])

    async def fake_connect(**kwargs):
        seen.append(kwargs)

    async def fake_get_db():
        return fake

    monkeypatch.setattr("app.core.db.client.connect", fake_connect)
    monkeypatch.setattr("app.core.db.client.get_db", fake_get_db)
    assert SyncDatabase()._handle() is fake
    assert seen == [{"probe": True}]


def test_el_progreso_queda_en_el_archivo_que_pide_el_orquestador(tmp_path, monkeypatch):
    path = tmp_path / "p.json"
    monkeypatch.setenv(sync_mod.PROGRESS_ENV, str(path))
    server = FakeServer()
    sdb = _bridge(server, [FakeConn(server)], pipelined=True)
    sdb["canonical_columns"].bulk_write([_up("a", 1), _up("b", 2)])
    sdb.drain()
    got = json.loads(path.read_text(encoding="utf-8"))
    assert (got["docs"], got["coleccion"]) == (2, "canonical_columns")
    assert "descartadas" in got and got["bytes"] > 0


# ── Revisión independiente (doc 110, ronda 1) ───────────────────────────────

def test_pipeline_despues_de_una_escritura_igual_usa_las_conexiones_pedidas():
    server = FakeServer()
    sdb = _bridge(server, [FakeConn(server) for _ in range(3)])
    sdb["c"].bulk_write([_up("a", 1)])               # modo espera: escritor de 1 conexión
    sdb.pipeline(workers=3)
    assert sdb._writer.workers == 3
    sdb["c"].bulk_write([_up("b", 1)])
    sdb.drain()
    assert sorted(server.rows) == ["a", "b"]


def test_una_lectura_espera_entre_intentos_y_pasa_un_corte_corto(monkeypatch):
    monkeypatch.setattr(sync_mod, "_READ_PAUSE", 0.1)
    server = FakeServer({"a": {"_id": "a", "v": 1}})
    sdb = _bridge(server, [])
    sdb._adb.down_until = time.monotonic() + 0.25
    assert sdb["c"].find_one({}) == {"_id": "a", "v": 1}


def test_un_error_ya_avisado_no_vuelve_a_saltar_en_lo_que_sigue():
    server = FakeServer({"z": {"_id": "z", "v": 0}})
    bad = FakeConn(server, fail=asyncpg.exceptions.DataError("bad json"), fail_times=1)
    sdb = _bridge(server, [bad])
    with pytest.raises(asyncpg.exceptions.DataError):
        sdb["c"].bulk_write([_up("a", 1)])
    assert sdb["c"].find_one({}) == {"_id": "z", "v": 0}
