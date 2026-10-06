"""Escritor de lotes RESISTENTE a la red para los scripts (doc 110).

Lo usa el puente sync (`app/core/db/sync.py`): one-shot, append, seeds. La app
NO pasa por acá (sigue con `PgCollection.bulk_write`).

Por qué: desde Lima, Lakebase (Azure East US 2) está a 130 ms y cerca de la
mitad de las conexiones cae en una ruta con 7-8 % de pérdida (50-70 KB/s contra
1.7-11 MB/s de las buenas). Con UNA conexión por archivo, el `migrate` de un XML
tardaba 44 min o moría con su conexión. Acá:

- Cada sentencia tiene un tiempo máximo según su tamaño; si se pasa, o la red o
  el servidor cortan la conexión, la conexión se TERMINA y la sentencia se
  repite en otra (el pool la abre probada: `pool.probing_connect`), con una
  pausa creciente entre intentos (un corte corto no agota los reintentos). Es
  seguro: sólo pasan por acá sentencias idempotentes por `_id`
  (`bulk_statements`).
- Antes de repetirla, se TERMINA en el servidor la sesión abandonada
  (`pg_terminate_backend`, por pid + inicio de la sesión: nunca otra que haya
  reusado el pid) y se espera a que salga: los bytes que el kernel ya había
  aceptado igual le llegan y la confirmaban DESPUÉS del reintento, encima de
  la versión siguiente del mismo `_id` (revisión del doc 110). La sesión se
  identifica con `pg_backend_pid()`: en Lakebase el pid que asyncpg recibe es
  del proxy. Requiere el endpoint DIRECTO (sesión dedicada por conexión, el
  de siempre: `pool.py`); detrás de un pooler, terminar una sesión cortaría a
  otros clientes. Si una escritura falla del todo, sus sesiones abandonadas se
  terminan antes de la próxima escritura (`_ghosts`).
- Varias conexiones a la vez, con reparto DINÁMICO: cada sentencia va a la
  conexión menos cargada (una lenta recibe poco), salvo que traiga un documento
  que otra conexión aún tiene pendiente: entonces va detrás de esa, así dos
  escrituras del mismo `_id` llegan en el orden en que se pidieron.
- Un error de datos no se reintenta: corta lo encolado detrás y se avisa en la
  siguiente escritura o al vaciar (`drain`), una sola vez. Nada falla en
  silencio.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from app.core.db.lakebase.collection import BulkStatement, run_statement
from app.core.db.lakebase.pool import NETWORK_ERRORS, is_network_error

log = logging.getLogger(__name__)

# Se repite en otra conexión; cualquier otro error de Postgres (datos,
# constraint) corta la carga.
RETRYABLE = NETWORK_ERRORS

# La sesión del servidor detrás de cada conexión (pid + inicio: un pid se puede
# reusar) y la terminación de una abandonada, esperando a que salga (≤ 5 s).
# Sin fila (ya no existe, u otra sesión tiene ese pid) = nada que terminar.
_SESSION_SQL = "SELECT pid, backend_start FROM pg_stat_activity WHERE pid = pg_backend_pid()"
_BURY_SQL = (
    "WITH t AS (SELECT pg_terminate_backend(pid, 5000) AS ok FROM pg_stat_activity "
    "WHERE pid = $1 AND backend_start = $2) SELECT coalesce(bool_and(ok), true) FROM t")


def _alive(conn: Any) -> bool:
    try:
        return not conn.is_closed()
    except Exception:  # noqa: BLE001 — proxy del pool ya devuelto: no sirve
        return False


def _discard(conn: Any) -> None:
    try:
        conn.terminate()
    except Exception:  # noqa: BLE001 — ya estaba cerrada
        pass


class ResilientWriter:
    """Corre las sentencias en `workers` conexiones propias (una cola por
    conexión, con tope: el productor espera si la red no da abasto)."""

    def __init__(self, pool: Any, *, workers: int = 1, min_timeout: float = 10.0,
                 min_rate: float = 200_000.0, retries: int = 6, backoff: float = 1.5,
                 retry_pause: float = 1.0, queue_size: int = 2,
                 on_progress: Callable[[dict], None] | None = None) -> None:
        self.workers = workers
        self._pool = pool
        self._min_timeout = min_timeout
        self._min_rate = min_rate          # bytes/s: por debajo, la conexión se da por mala
        self._retries = retries
        self._backoff = backoff
        self._retry_pause = retry_pause    # s antes del 2.º intento; se duplica (tope 30 s)
        self._queue_size = queue_size
        self._on_progress = on_progress
        self._queues: list[asyncio.Queue] = []
        self._tasks: list[asyncio.Task | None] = []
        self._conns: list[Any] = []        # la conexión que tiene tomada cada worker
        self._session: list[tuple | None] = []   # (pid, backend_start) de cada una
        # Sesiones que recibieron una sentencia que al final FALLÓ (reintentos
        # agotados o error): se terminan antes de la próxima escritura.
        self._ghosts: set[tuple] = set()
        self._load: list[int] = []         # sentencias en cola + en curso, por worker
        self._pending: dict[str, list[int]] = {}   # _id → [worker, sentencias pendientes con él]
        self._freed: asyncio.Event | None = None   # se marca cada vez que termina una sentencia
        self._error: BaseException | None = None
        self._t0: float | None = None
        self.stats: dict[str, Any] = {"docs": 0, "bytes": 0, "sentencias": 0,
                                      "reintentos": 0, "coleccion": None}

    async def submit(self, coll: Any, statements: list[BulkStatement], *,
                     results: bool = False) -> list[asyncio.Future]:
        """Encola las sentencias (en orden). Con `results`, devuelve un futuro
        por sentencia (filas que ya existían)."""
        self._raise_if_failed()
        if not self._queues:
            self._queues = [asyncio.Queue(maxsize=self._queue_size) for _ in range(self.workers)]
            self._tasks = [None] * self.workers
            self._conns = [None] * self.workers
            self._session = [None] * self.workers
            self._load = [0] * self.workers
            self._freed = asyncio.Event()
        await coll.database.ensure_table(coll.name)
        loop = asyncio.get_running_loop()
        futs: list[asyncio.Future] = []
        for st in statements:
            ids = st.args[0]
            k = await self._route(ids)
            self._load[k] += 1
            for _id in ids:
                slot = self._pending.setdefault(_id, [k, 0])
                slot[1] += 1
            fut = loop.create_future() if results else None
            try:
                await self._queues[k].put((coll, st, fut))
            except BaseException:          # cancelado esperando lugar: no queda pendiente
                self._release(k, ids)
                raise
            self._wake(k)
            if fut is not None:
                futs.append(fut)
        return futs

    async def _route(self, ids: list[str]) -> int:
        """Conexión para una sentencia: detrás de la que tenga pendiente alguno
        de sus documentos (si son de dos o más, espera a que queden en una); si
        no, la menos cargada con lugar en su cola."""
        assert self._freed is not None
        while True:
            self._freed.clear()
            owners = {self._pending[i][0] for i in ids if i in self._pending}
            if len(owners) == 1:
                return owners.pop()
            if not owners:
                free = [k for k in range(self.workers) if not self._queues[k].full()]
                if free:
                    return min(free, key=lambda k: self._load[k])
            await self._freed.wait()

    def _wake(self, k: int) -> None:
        """Un worker por cola, sólo mientras tiene trabajo: sin trabajo no queda
        ninguna tarea viva (al salir el proceso no hay nada pendiente)."""
        task = self._tasks[k]
        if task is None or task.done():
            self._tasks[k] = asyncio.get_running_loop().create_task(self._worker(k))

    async def drain(self) -> None:
        """Espera a que todo lo encolado esté escrito y devuelve las conexiones
        al pool (si no, `pool.close()` las esperaría para siempre); propaga el
        primer error."""
        await asyncio.gather(*(q.join() for q in self._queues))
        await asyncio.gather(*(t for t in self._tasks if t is not None))
        for k, conn in enumerate(self._conns):
            if conn is not None:
                self._conns[k] = self._session[k] = None
                try:
                    # Con tope: una conexión muda al devolverla no cuelga la carga.
                    await self._pool.release(conn, timeout=self._min_timeout)
                except Exception:  # noqa: BLE001 — ya estaba cerrada o no respondió
                    _discard(conn)
        error, self._error = self._error, None    # se avisa una vez
        if error is not None:
            raise error

    def _raise_if_failed(self) -> None:
        if self._error is not None:
            raise self._error

    async def _worker(self, k: int) -> None:
        q = self._queues[k]
        # Sin `await` entre «cola vacía» y el fin de la tarea: un `submit`
        # posterior ve la tarea terminada y despierta otra (`_wake`).
        while not q.empty():
            coll, st, fut = q.get_nowait()
            try:
                if self._error is not None:          # la carga ya se cortó
                    if fut is not None and not fut.done():
                        fut.set_exception(self._error)
                    continue
                n = await self._run(k, coll, st)
                if fut is not None and not fut.done():
                    fut.set_result(n)
            except Exception as exc:  # noqa: BLE001 — el primero corta la carga y se propaga
                if self._error is None:
                    self._error = exc
                if fut is not None and not fut.done():
                    fut.set_exception(exc)
            finally:
                self._done(k, st)
                q.task_done()

    def _done(self, k: int, st: BulkStatement) -> None:
        self._release(k, st.args[0])

    def _release(self, k: int, ids: list[str]) -> None:
        self._load[k] -= 1
        for _id in ids:
            slot = self._pending.get(_id)
            if slot is not None:
                slot[1] -= 1
                if slot[1] <= 0:
                    del self._pending[_id]
        assert self._freed is not None
        self._freed.set()

    async def _run(self, k: int, coll: Any, st: BulkStatement) -> int:
        abandoned: list[tuple] = []        # sesiones que recibieron esta sentencia y se dejaron
        try:
            return await self._attempts(k, coll, st, abandoned)
        except BaseException:
            # Si el script sigue (el error se avisa una vez), lo que estas
            # sesiones aún puedan confirmar no debe caer encima de lo siguiente.
            self._ghosts.update(abandoned)
            raise

    async def _attempts(self, k: int, coll: Any, st: BulkStatement, abandoned: list[tuple]) -> int:
        budget = max(self._min_timeout, st.nbytes / self._min_rate)
        last: BaseException | None = None
        for attempt in range(1, self._retries + 1):
            sent = False
            try:
                if self._conns[k] is None or not _alive(self._conns[k]):
                    await self._acquire(k)
                for ghost in list(self._ghosts):
                    await self._bury(self._conns[k], ghost)
                    self._ghosts.discard(ghost)
                while abandoned:
                    await self._bury(self._conns[k], abandoned[0])
                    abandoned.pop(0)
                if self._t0 is None:
                    self._t0 = time.monotonic()
                sent = True
                n = await asyncio.wait_for(run_statement(self._conns[k], st), budget)
            except RETRYABLE as exc:
                if not is_network_error(exc):
                    raise                  # p. ej. un parámetro que no se puede codificar
                last = exc
                if sent and self._session[k] is not None:
                    abandoned.append(self._session[k])
                if self._conns[k] is not None:
                    _discard(self._conns[k])
                self._conns[k] = self._session[k] = None
                self.stats["reintentos"] += 1
                log.warning(
                    "Lakebase: el lote de %s (%d docs, %.1f MB) no terminó en %.0f s (%s); "
                    "se repite en otra conexión (%d/%d)",
                    coll.name, st.ndocs, st.nbytes / 1e6, budget, type(exc).__name__,
                    attempt, self._retries)
                budget *= self._backoff
                self._progress()
                if attempt < self._retries:
                    await asyncio.sleep(min(30.0, self._retry_pause * 2 ** (attempt - 1)))
                continue
            self.stats["docs"] += st.ndocs
            self.stats["bytes"] += st.nbytes
            self.stats["sentencias"] += 1
            self.stats["coleccion"] = coll.name
            self._progress()
            return n
        raise RuntimeError(
            f"Lakebase: el lote de {coll.name} ({st.ndocs} docs) falló {self._retries} veces "
            f"seguidas por la red; último error: {last!r}") from last

    async def _acquire(self, k: int) -> None:
        """Conexión nueva (probada por el pool) y la sesión del servidor detrás."""
        self._conns[k] = await self._pool.acquire()
        row = await asyncio.wait_for(self._conns[k].fetchrow(_SESSION_SQL), self._min_timeout)
        self._session[k] = (row["pid"], row["backend_start"]) if row else None

    async def _bury(self, conn: Any, session: tuple) -> None:
        """Termina la sesión abandonada y espera a que salga; si sigue viva, la
        sentencia no se repite encima (este intento cuenta como fallido)."""
        pid, started = session
        gone = await asyncio.wait_for(conn.fetchval(_BURY_SQL, pid, started), self._min_timeout + 5)
        if not gone:
            raise TimeoutError(f"la sesión {pid} que recibió la sentencia sigue viva")

    def _progress(self) -> None:
        if self._on_progress is None:
            return
        secs = time.monotonic() - self._t0 if self._t0 is not None else 0.0
        kbps = self.stats["bytes"] / 1024 / secs if secs > 0 else 0.0
        self._on_progress({**self.stats, "kbps": round(kbps)})
