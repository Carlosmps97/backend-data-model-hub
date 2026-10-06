"""Doc 110 — escritor de lotes RESISTENTE a la red (scripts: one-shot, append, seeds).

En el one-shot del 2026-10-05 la mitad de las conexiones a Lakebase caía en una
ruta con 7-8 % de pérdida (50-70 KB/s) y el `migrate` de un archivo usaba UNA
sola conexión todo el tiempo: 44 min un archivo, y a «Otros» se le murió la
conexión a mitad de las columnas. El escritor corta la sentencia que pasa su
tiempo máximo (o cuya conexión se cae) y la repite en OTRA conexión; reparte por
`_id` en varias conexiones sin perder el orden de cada documento.

Postgres falso con la semántica real del upsert por lote (UPDATE de lo que
existe, INSERT de los upserts faltantes); el pool entrega conexiones de un guion."""
from __future__ import annotations

import asyncio
import json
import time

import asyncpg
import pytest
from pymongo import DeleteOne, UpdateOne

from app.core.db.lakebase.writer import ResilientWriter
from tests.lakebase.test_adapter_bulk_doc105 import _coll, _Recorder


class FakeServer:
    """Postgres falso: aplica el upsert por lote con su semántica real y lleva
    las sesiones vivas (pid + inicio) para `pg_terminate_backend`."""

    def __init__(self, rows: dict | None = None) -> None:
        self.rows: dict[str, dict] = rows or {}
        self.backends: dict[int, float] = {}      # pid → backend_start de las vivas
        self.killed: list[int] = []
        self.unkillable: set[int] = set()
        self.bury_error: BaseException | None = None   # p. ej. sin permiso para terminar
        self._pid = 1000

    def new_backend(self) -> tuple[int, float]:
        self._pid += 1
        self.backends[self._pid] = float(self._pid)
        return self._pid, float(self._pid)

    def terminate(self, pid: int, start: float) -> bool:
        if self.backends.get(pid) != start:
            return True                           # ya no existe (o es OTRA sesión con ese pid)
        if pid in self.unkillable:
            return False
        del self.backends[pid]
        self.killed.append(pid)
        return True

    def run(self, sql: str, args: tuple) -> dict:
        assert "AS u(uid, patch, soi, ups)" in sql, sql
        ids, patches, sois, ups = args
        before = set(self.rows)
        updated = inserted = 0
        for i, p in zip(ids, patches):
            if i in before:
                self.rows[i] = {**self.rows[i], **json.loads(p)}
                updated += 1
        for i, p, o, u in zip(ids, patches, sois, ups):
            if u and i not in before and i not in self.rows:
                self.rows[i] = {"_id": i, **json.loads(o), **json.loads(p)}
                inserted += 1
        return {"updated": updated, "inserted": inserted}


class FakeConn:
    """`stall`: la sentencia nunca responde (ruta perdida). `late`: además, la
    sentencia LLEGA al servidor y se confirma `late` s después, salvo que su
    sesión haya sido terminada (bytes que el kernel ya había aceptado).
    `fail`: la conexión lanza ese error (`fail_times` veces; None = siempre).
    `gate`: responde recién cuando se abre el evento. `reuse_pid`: al
    terminarla, otra sesión toma su pid."""

    def __init__(self, server: FakeServer, *, stall=False, late=None, fail=None, fail_times=None,
                 delay=0.0, gate=None, reuse_pid=False) -> None:
        self.server, self.stall, self.late, self.fail = server, stall or late is not None, late, fail
        self.fail_times, self.delay, self.gate, self.reuse_pid = fail_times, delay, gate, reuse_pid
        self.pid, self.start = server.new_backend()
        self.closed = False
        self.ran = 0

    async def fetchrow(self, sql, *args):
        if "pg_backend_pid()" in sql:
            return {"pid": self.pid, "backend_start": self.start}
        if self.late is not None:
            def commit_late():
                if self.server.backends.get(self.pid) == self.start:
                    self.server.run(sql, args)
            asyncio.get_running_loop().call_later(self.late, commit_late)
        if self.stall:
            await asyncio.sleep(3600)
        if self.gate is not None:
            await self.gate.wait()
        await asyncio.sleep(self.delay)
        if self.fail is not None and (self.fail_times is None or self.fail_times > 0):
            if self.fail_times is not None:
                self.fail_times -= 1
            raise self.fail
        self.ran += 1
        return self.server.run(sql, args)

    async def fetchval(self, sql, *args):
        assert "pg_terminate_backend" in sql, sql
        if self.server.bury_error is not None:
            raise self.server.bury_error
        return self.server.terminate(*args)

    def terminate(self) -> None:
        self.closed = True
        if self.reuse_pid:                          # la sesión salió y otra tomó su pid
            self.server.backends[self.pid] = self.start + 0.5

    def is_closed(self) -> bool:
        return self.closed


class FakePool:
    """Entrega conexiones de un guion; una devuelta (`release`) se vuelve a
    entregar antes que las nuevas, como el pool de asyncpg."""

    def __init__(self, conns: list[FakeConn]) -> None:
        self.conns = list(conns)
        self.handed: list[FakeConn] = []
        self.released: list[FakeConn] = []

    async def acquire(self) -> FakeConn:
        conn = self.conns.pop(0)
        self.handed.append(conn)
        return conn

    async def release(self, conn: FakeConn, timeout=None) -> None:
        self.released.append(conn)
        self.conns.insert(0, conn)


def _up(_id: str, v) -> UpdateOne:
    return UpdateOne({"_id": _id}, {"$set": {"v": v}, "$setOnInsert": {"c": 1}}, upsert=True)


def _writer(pool, **kw) -> ResilientWriter:
    kw = {"min_timeout": 0.05, "min_rate": 1e12, "retries": 3, "retry_pause": 0.0, **kw}
    return ResilientWriter(pool, **kw)


async def _write(writer: ResilientWriter, ops, *, max_bytes=None, results=False):
    coll = _coll(_Recorder())
    return await writer.submit(coll, coll.bulk_statements(ops, max_bytes), results=results)


# ── reintento en otra conexión ──────────────────────────────────────────────

def test_un_lote_que_se_cuelga_se_reintenta_en_otra_conexion_y_queda_una_vez():
    server = FakeServer()
    pool = FakePool([FakeConn(server, stall=True), FakeConn(server)])

    async def go():
        w = _writer(pool)
        await _write(w, [_up("a", 1), _up("b", 2)])
        await w.drain()
        return w

    w = asyncio.run(go())
    assert server.rows == {"a": {"_id": "a", "c": 1, "v": 1}, "b": {"_id": "b", "c": 1, "v": 2}}
    assert pool.handed[0].closed and pool.handed[1].ran == 1
    assert w.stats["reintentos"] == 1


@pytest.mark.parametrize("err", [
    ConnectionResetError(54, "Connection reset by peer"),
    TimeoutError(60, "Operation timed out"),
    asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed in the middle of operation"),
    asyncpg.exceptions.AdminShutdownError("terminating connection due to administrator command"),
])
def test_si_la_red_o_el_servidor_cortan_la_conexion_tambien_se_reintenta(err):
    server = FakeServer()
    pool = FakePool([FakeConn(server, fail=err), FakeConn(server)])

    async def go():
        w = _writer(pool)
        await _write(w, [_up("a", 1)])
        await w.drain()

    asyncio.run(go())
    assert server.rows == {"a": {"_id": "a", "c": 1, "v": 1}}
    assert pool.handed[0].closed


def test_un_error_de_datos_no_se_reintenta_y_se_avisa_una_sola_vez():
    """Corta lo encolado detrás y se avisa al vaciar; después (un script que lo
    atrapó) se puede seguir escribiendo y leyendo."""
    server = FakeServer()
    bad = FakeConn(server, fail=asyncpg.exceptions.DataError("invalid input syntax for type json"),
                   fail_times=1)
    pool = FakePool([bad, FakeConn(server)])

    async def go():
        w = _writer(pool)
        await _write(w, [_up("a", 1)])
        with pytest.raises(asyncpg.exceptions.DataError):
            await w.drain()
        await _write(w, [_up("b", 2)])
        await w.drain()

    asyncio.run(go())
    assert all(c is bad for c in pool.handed)        # nunca se probó en otra conexión
    assert server.rows == {"b": {"_id": "b", "c": 1, "v": 2}}


def test_agotados_los_reintentos_falla_con_un_error_que_nombra_la_coleccion():
    server = FakeServer()
    pool = FakePool([FakeConn(server, stall=True) for _ in range(3)])

    async def go():
        w = _writer(pool, retries=3)
        await _write(w, [_up("a", 1)])
        await w.drain()

    with pytest.raises(RuntimeError, match=r"canonical_columns.*3 veces"):
        asyncio.run(go())
    assert all(c.closed for c in pool.handed) and len(pool.handed) == 3


# ── varias conexiones ───────────────────────────────────────────────────────

def test_con_tres_conexiones_cada_documento_conserva_su_orden():
    """Una conexión lenta y dos instantáneas, tres versiones de 31 documentos
    en sentencias de ~3: si una versión vieja fuera por la lenta y la nueva por
    otra, la vieja llegaría última."""
    server = FakeServer()
    pool = FakePool([FakeConn(server, delay=d) for d in (0.05, 0.0, 0.0)])
    ids = [f"doc{i}" for i in range(31)]

    async def go():
        w = _writer(pool, workers=3, min_timeout=5)
        for version in (1, 2, 3):
            await _write(w, [_up(i, version) for i in ids], max_bytes=60)
        await w.drain()

    asyncio.run(go())
    assert {r["v"] for r in server.rows.values()} == {3} and len(server.rows) == 31
    assert sum(1 for c in pool.handed if c.ran) >= 2        # el trabajo se repartió


def test_documentos_pendientes_en_dos_conexiones_esperan_y_conservan_su_orden():
    server = FakeServer()
    pool = FakePool([FakeConn(server, delay=0.05), FakeConn(server, delay=0.1), FakeConn(server)])

    async def go():
        w = _writer(pool, workers=3, min_timeout=5)
        await _write(w, [_up("a", 1)])                     # → conexión 1 (lenta)
        await _write(w, [_up("b", 1)])                     # → conexión 2 (más lenta)
        await _write(w, [_up("a", 2), _up("b", 2)])        # a y b pendientes en dos conexiones
        await w.drain()

    asyncio.run(go())
    assert (server.rows["a"]["v"], server.rows["b"]["v"]) == (2, 2)


def test_una_conexion_lenta_recibe_poco_trabajo():
    """Reparto dinámico: con una conexión lenta y dos rápidas, la lenta no
    retiene un tercio fijo del lote (60 sentencias × 0.3 s serían 6 s)."""
    server = FakeServer()
    slow = FakeConn(server, delay=0.3)
    pool = FakePool([slow, FakeConn(server), FakeConn(server)])

    async def go():
        w = _writer(pool, workers=3, min_timeout=5)
        await _write(w, [_up(f"d{i:03d}", 1) for i in range(180)], max_bytes=60)
        await w.drain()

    t0 = time.monotonic()
    asyncio.run(go())
    assert len(server.rows) == 180
    assert slow.ran <= 3 and time.monotonic() - t0 < 2.0


def test_lo_no_idempotente_por_id_no_tiene_sentencias():
    assert _coll(_Recorder()).bulk_statements([DeleteOne({"_id": "a"})], 100) is None


def test_cola_llena_frena_al_productor():
    server = FakeServer()

    async def go():
        gate = asyncio.Event()
        w = _writer(FakePool([FakeConn(server, gate=gate)]), queue_size=1, min_timeout=5)
        await _write(w, [_up("a", 1)])                    # en curso
        await asyncio.sleep(0.01)
        await _write(w, [_up("b", 1)])                    # en cola (llena)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(_write(w, [_up("c", 1)]), 0.05)
        gate.set()
        await _write(w, [_up("c", 1)])
        await w.drain()

    asyncio.run(go())
    assert sorted(server.rows) == ["a", "b", "c"]


def test_al_vaciar_devuelve_sus_conexiones_y_las_retoma_despues():
    """Sin esto, `pool.close()` (el `disconnect` de un script) esperaría para
    siempre a las conexiones que el escritor tiene tomadas."""
    server = FakeServer()
    pool = FakePool([FakeConn(server)])

    async def go():
        w = _writer(pool)
        await _write(w, [_up("a", 1)])
        await w.drain()
        assert pool.released == pool.handed
        await _write(w, [_up("b", 1)])
        await w.drain()

    asyncio.run(go())
    assert sorted(server.rows) == ["a", "b"] and len(pool.released) == 2


def test_sin_trabajo_no_deja_tareas_vivas():
    """Al salir el proceso, una tarea viva del escritor imprimiría «Task was
    destroyed but it is pending!» al final de cada migrate."""
    server = FakeServer()

    async def go():
        w = _writer(FakePool([FakeConn(server, delay=0.01) for _ in range(3)]), workers=3)
        await _write(w, [_up(f"d{i}", 1) for i in range(10)])
        await w.drain()
        await asyncio.sleep(0)
        return {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}

    assert asyncio.run(go()) == set()
    assert len(server.rows) == 10


# ── resultado y progreso ────────────────────────────────────────────────────

def test_en_modo_espera_devuelve_las_filas_que_ya_existian():
    server = FakeServer({"a": {"_id": "a", "v": 0}})

    async def go():
        w = _writer(FakePool([FakeConn(server)]))
        futs = await _write(w, [_up("a", 1), _up("b", 1)], results=True)
        return await asyncio.gather(*futs)

    assert asyncio.run(go()) == [1]
    assert server.rows["a"] == {"_id": "a", "v": 1}


def test_el_progreso_cuenta_documentos_bytes_reintentos_y_coleccion():
    server = FakeServer()
    seen: list[dict] = []

    async def go():
        w = _writer(FakePool([FakeConn(server, stall=True), FakeConn(server)]), on_progress=seen.append)
        await _write(w, [_up("a", "x" * 100), _up("b", "y" * 100)])
        await w.drain()

    asyncio.run(go())
    last = seen[-1]
    assert (last["docs"], last["reintentos"], last["coleccion"]) == (2, 1, "canonical_columns")
    assert last["bytes"] > 200


# ── Revisión independiente (doc 110, ronda 1) ───────────────────────────────

def test_una_sentencia_abandonada_no_se_confirma_despues_de_su_reintento():
    """ALTO: al cortar una sentencia lenta, los bytes que el kernel ya había
    aceptado igual llegan; la sesión vieja la confirmaba DESPUÉS del reintento y
    de la versión siguiente del mismo `_id` (quedaba la vieja). Antes de
    reintentar, se termina esa sesión."""
    server = FakeServer()
    ghost = FakeConn(server, late=0.2)
    pool = FakePool([ghost, FakeConn(server)])

    async def go():
        w = _writer(pool)
        await _write(w, [_up("a", 1)])
        await w.drain()
        await _write(w, [_up("a", 2)])
        await w.drain()
        await asyncio.sleep(0.4)                      # la abandonada habría llegado a los 0.2 s

    asyncio.run(go())
    assert server.rows["a"]["v"] == 2
    assert ghost.pid in server.killed


def test_no_termina_otra_sesion_que_reuso_el_pid_de_la_abandonada():
    server = FakeServer()
    ghost = FakeConn(server, stall=True, reuse_pid=True)
    pool = FakePool([ghost, FakeConn(server)])

    async def go():
        w = _writer(pool)
        await _write(w, [_up("a", 1)])
        await w.drain()

    asyncio.run(go())
    assert server.rows["a"]["v"] == 1
    assert ghost.pid not in server.killed and server.backends[ghost.pid] == ghost.start + 0.5


def test_si_la_sesion_abandonada_no_muere_no_se_reintenta_encima():
    server = FakeServer()
    ghost = FakeConn(server, stall=True)
    server.unkillable.add(ghost.pid)
    pool = FakePool([ghost] + [FakeConn(server) for _ in range(3)])

    async def go():
        w = _writer(pool, retries=3)
        await _write(w, [_up("a", 1)])
        await w.drain()

    with pytest.raises(RuntimeError, match="canonical_columns"):
        asyncio.run(go())
    assert "a" not in server.rows


def test_vaciar_no_se_cuelga_si_devolver_una_conexion_no_responde():
    server = FakeServer()

    class SilentPool(FakePool):
        async def release(self, conn, timeout=None):
            await asyncio.wait_for(asyncio.sleep(3600), timeout)

    async def go():
        w = _writer(SilentPool([FakeConn(server)]))
        await _write(w, [_up("a", 1)])
        await asyncio.wait_for(w.drain(), 2)

    asyncio.run(go())
    assert server.rows["a"]["v"] == 1


def test_un_corte_corto_de_la_red_no_agota_los_reintentos():
    """Sin pausa entre intentos, un corte de 0.3 s (DNS, sin ruta) gastaba los
    6 intentos en milisegundos y cortaba la carga."""
    server = FakeServer()

    class DownPool(FakePool):
        async def acquire(self):
            if time.monotonic() < self.up_at:
                raise OSError(65, "No route to host")
            return await super().acquire()

    pool = DownPool([FakeConn(server)])
    pool.up_at = time.monotonic() + 0.3

    async def go():
        w = _writer(pool, retries=6, retry_pause=0.05)
        await _write(w, [_up("a", 1)])
        await w.drain()

    asyncio.run(go())
    assert server.rows["a"]["v"] == 1


def test_un_envio_cancelado_no_deja_documentos_pendientes():
    """Un `submit` cancelado esperando lugar en la cola dejaba sus documentos
    como «pendientes» en dos conexiones: el siguiente con ambos esperaba para
    siempre."""
    server = FakeServer()

    async def go():
        gate = asyncio.Event()
        w = _writer(FakePool([FakeConn(server, gate=gate), FakeConn(server, gate=gate)]),
                    workers=2, queue_size=1, min_timeout=5)
        for v in (1, 2):
            await _write(w, [_up("x", v)])
            await _write(w, [_up("y", v)])
            await asyncio.sleep(0.01)
        for doc in ("x", "y"):
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(_write(w, [_up(doc, 3)]), 0.05)
        gate.set()
        await w.drain()
        await asyncio.wait_for(_write(w, [_up("x", 4), _up("y", 4)]), 1)
        await w.drain()

    asyncio.run(go())
    assert (server.rows["x"]["v"], server.rows["y"]["v"]) == (4, 4)


# ── Revisión independiente (doc 110, ronda 2) ───────────────────────────────

def test_la_sesion_de_una_escritura_que_fallo_del_todo_se_termina_antes_de_la_siguiente():
    """Si una escritura agota sus reintentos y el script sigue (el error se
    avisa una vez), la sesión que recibió su último intento se termina antes de
    escribir lo siguiente: si no, confirmaba tarde, encima."""
    server = FakeServer()
    g1, g2 = FakeConn(server, late=0.2), FakeConn(server, late=0.2)
    pool = FakePool([g1, g2, FakeConn(server)])

    async def go():
        w = _writer(pool, retries=2)
        await _write(w, [_up("a", 1)])
        with pytest.raises(RuntimeError):
            await w.drain()
        await _write(w, [_up("a", 2)])
        await w.drain()
        await asyncio.sleep(0.4)

    asyncio.run(go())
    assert server.rows["a"]["v"] == 2
    assert {g1.pid, g2.pid} <= set(server.killed)


def test_si_no_se_puede_terminar_la_sesion_abandonada_no_se_escribe_encima_sin_avisar():
    server = FakeServer()
    server.bury_error = asyncpg.exceptions.InsufficientPrivilegeError("permission denied to terminate process")
    pool = FakePool([FakeConn(server, late=0.2), FakeConn(server), FakeConn(server)])

    async def go():
        w = _writer(pool)
        await _write(w, [_up("a", 1)])
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await w.drain()
        await _write(w, [_up("a", 2)])
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await w.drain()

    asyncio.run(go())


def test_un_error_al_codificar_los_parametros_no_se_trata_como_red():
    """El `DataError` del CLIENTE de asyncpg (parámetro que no se puede
    codificar) hereda de InterfaceError: no es la red, no se reintenta."""
    from asyncpg.exceptions import _base

    server = FakeServer()
    bad = FakeConn(server, fail=_base.DataError("invalid input for query argument $2"), fail_times=1)
    pool = FakePool([bad, FakeConn(server)])

    async def go():
        w = _writer(pool)
        await _write(w, [_up("a", 1)])
        with pytest.raises(_base.DataError):
            await w.drain()

    asyncio.run(go())
    assert pool.handed == [bad] and server.killed == []
