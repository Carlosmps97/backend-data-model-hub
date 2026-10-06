"""Puente SYNC a la BD (Lakebase) para scripts (audit, migrate --apply, backfills, fixes).

Los ~10 scripts sync del repo se conectan a la BD vía `get_sync_db()`, un
wrapper sync PEREZOSO sobre el adaptador async (event loop dedicado en un thread
de fondo). Perezoso porque varios scripts construyen el handle a nivel de módulo
(import) y recién deben conectar al primer uso.

Superficie soportada (la que usan los scripts): acceso por índice Y por
atributo (`db["views"]` / `db.views`), find (iterable, con sort/limit/skip),
find_one, insert/update/replace/delete, distinct, count_documents, aggregate,
bulk_write, drop, create_index, list_collection_names, command.

Doc 110 — resistente a la red (la app no pasa por acá):
- conexiones PROBADAS (`connect(probe=True)`): una en ruta lenta se descarta;
- `bulk_write` idempotente por `_id` va por el escritor resistente
  (`lakebase/writer.py`): tiempo máximo por sentencia y reintento en otra
  conexión. Por defecto espera el resultado; con `pipeline()` (migrate) encola
  y vuelve, por varias conexiones;
- toda lectura u otra escritura vacía antes la cola (`drain`): se lee lo ya
  escrito y el orden se respeta; una lectura que pierde la conexión se repite;
- progreso en el archivo de `DMH_PROGRESS_FILE` (lo lee el orquestador).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from typing import Any

from app.core.db.lakebase.collection import _WriteResult
from app.core.db.lakebase.pool import NETWORK_ERRORS, PROBE_STATS, is_network_error
from app.core.db.lakebase.writer import ResilientWriter

log = logging.getLogger(__name__)

_loop: asyncio.AbstractEventLoop | None = None
_db: "SyncDatabase | None" = None
_lock = threading.Lock()

PROGRESS_ENV = "DMH_PROGRESS_FILE"
PIPELINE_WORKERS = 3                     # conexiones con las que escribe el migrate
MAX_STATEMENT_BYTES = 2 * 1024 * 1024    # tope de una sentencia del escritor
_READ_TRIES = 3
_READ_PAUSE = 1.0        # s antes del 2.º intento de una lectura; se duplica
_READS = {"find_one", "count_documents", "distinct", "estimated_document_count"}


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _lock:
        if _loop is None:
            _loop = asyncio.new_event_loop()
            threading.Thread(
                target=_loop.run_forever, daemon=True, name="lakebase-sync-loop"
            ).start()
    return _loop


def _run(coro: Any) -> Any:
    return asyncio.run_coroutine_threadsafe(coro, _ensure_loop()).result()


async def _gather(futs: list) -> list:
    return await asyncio.gather(*futs, return_exceptions=True)


def get_sync_db():
    """Handle sync de la base (Lakebase; ver docstring del módulo)."""
    global _db
    if _db is None:
        _db = SyncDatabase()
    return _db


class _ProgressFile:
    """Doc 110: deja el avance del escritor en el archivo que pide el
    orquestador (como mucho cada 2 s y siempre al vaciar la cola)."""

    def __init__(self) -> None:
        self._last = 0.0

    def write(self, stats: dict, force: bool = False) -> None:
        path = os.environ.get(PROGRESS_ENV)
        now = time.monotonic()
        if not path or (not force and now - self._last < 2.0):
            return
        self._last = now
        tmp = f"{path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({**stats, "descartadas": PROBE_STATS["descartadas"]}, fh)
            os.replace(tmp, path)
        except OSError:
            pass    # el progreso es informativo: nunca frena la carga


class SyncDatabase:
    """Wrapper sync PEREZOSO del LakebaseDatabase (conecta al primer uso)."""

    def __init__(self) -> None:
        self._adb: Any | None = None
        self._connect_lock = threading.Lock()
        self._writer: ResilientWriter | None = None
        self._pipelined = False
        self._progress = _ProgressFile()

    def _handle(self) -> Any:
        if self._adb is None:
            with self._connect_lock:
                if self._adb is None:
                    from app.core.db import client as db_client

                    async def _connect():
                        # Doc 110: conexiones probadas (las lentas se descartan).
                        await db_client.connect(probe=True)
                        return await db_client.get_db()

                    self._adb = _run(_connect())
        return self._adb

    def pipeline(self, workers: int = PIPELINE_WORKERS) -> None:
        """Doc 110: `bulk_write` encola y vuelve, escribiendo por `workers`
        conexiones (un `_id` pendiente siempre detrás de su conexión). `drain()`
        espera lo encolado (las lecturas lo hacen solas)."""
        if self._writer is not None and self._writer.workers != workers:
            self.drain()                  # el de antes (otra cantidad) termina lo suyo
            self._writer = None
        self._writer_for(workers)
        self._pipelined = True

    def drain(self) -> None:
        """Espera a que lo encolado esté escrito; propaga el primer error."""
        if self._writer is not None:
            try:
                _run(self._writer.drain())
            finally:
                self._progress.write(self._writer.stats, force=True)

    def _writer_for(self, workers: int = 1) -> ResilientWriter:
        if self._writer is None:
            self._writer = ResilientWriter(self._handle().pool, workers=workers,
                                           on_progress=self._progress.write)
        return self._writer

    def _read(self, factory) -> Any:
        """Lectura: después de lo encolado y, si pierde la conexión, otra vez."""
        self.drain()
        for attempt in range(1, _READ_TRIES + 1):
            try:
                return _run(factory())
            except NETWORK_ERRORS as exc:
                if attempt == _READ_TRIES or not is_network_error(exc):
                    raise
                log.warning("Lakebase: una lectura perdió la conexión (%s); se repite (%d/%d)",
                            type(exc).__name__, attempt, _READ_TRIES)
                time.sleep(_READ_PAUSE * 2 ** (attempt - 1))   # un corte corto no agota los intentos

    def __getitem__(self, name: str) -> "SyncCollection":
        return SyncCollection(self, name)

    def __getattr__(self, name: str) -> "SyncCollection":
        if name.startswith("_"):
            raise AttributeError(name)
        return SyncCollection(self, name)

    def list_collection_names(self) -> list[str]:
        return self._read(lambda: self._handle().list_collection_names())

    def command(self, cmd: Any) -> dict:
        self.drain()
        return _run(self._handle().command(cmd))


class SyncCursor:
    def __init__(self, acursor: Any, sdb: SyncDatabase) -> None:
        self._c = acursor
        self._sdb = sdb

    def sort(self, key_or_list, direction=None) -> "SyncCursor":
        self._c = self._c.sort(key_or_list, direction)
        return self

    def skip(self, n: int) -> "SyncCursor":
        self._c = self._c.skip(n)
        return self

    def limit(self, n: int) -> "SyncCursor":
        self._c = self._c.limit(n)
        return self

    def __iter__(self):
        return iter(self._sdb._read(lambda: self._c.to_list(None)))


class SyncCollection:
    def __init__(self, sdb: SyncDatabase, name: str) -> None:
        self._sdb = sdb
        self._name = name

    @property
    def _a(self) -> Any:
        return self._sdb._handle()[self._name]

    def find(self, *args: Any, **kwargs: Any) -> SyncCursor:
        return SyncCursor(self._a.find(*args, **kwargs), self._sdb)

    def aggregate(self, pipeline: list[dict], **kwargs: Any) -> list[dict]:
        acoll = self._a
        return self._sdb._read(lambda: acoll.aggregate(pipeline, **kwargs).to_list(None))

    def bulk_write(self, ops: Any, ordered: bool = True) -> Any:
        """Doc 110: lo idempotente por `_id` va por el escritor resistente (en
        cola con `pipeline()`: devuelve None); lo demás, por el camino de
        siempre, después de lo encolado. Tras un reintento cuyo primer intento
        sí se había confirmado, `matched_count` cuenta también lo que ese
        intento insertó (ningún script lo usa para decidir)."""
        sdb, acoll, ops = self._sdb, self._a, list(ops)
        statements = acoll.bulk_statements(ops, MAX_STATEMENT_BYTES)
        if statements is None:
            sdb.drain()
            return _run(acoll.bulk_write(ops, ordered=ordered))
        writer = sdb._writer_for()
        if sdb._pipelined:
            _run(writer.submit(acoll, statements))
            return None
        done = _run(_gather(_run(writer.submit(acoll, statements, results=True))))
        sdb.drain()      # devuelve las conexiones al pool y propaga el error de la cola
        errors = [d for d in done if isinstance(d, BaseException)]
        if errors:
            raise errors[0]
        return _WriteResult(matched=sum(done), modified=sum(done))

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        # Métodos async 1:1 (find_one, insert_one, update_one, delete_many,
        # distinct, count_documents, drop, create_index, …), siempre después de
        # lo encolado; las lecturas se repiten si pierden la conexión.
        target = getattr(self._a, name)

        def call(*args: Any, **kwargs: Any) -> Any:
            if name in _READS:
                return self._sdb._read(lambda: target(*args, **kwargs))
            self._sdb.drain()
            return _run(target(*args, **kwargs))

        return call
