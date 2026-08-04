"""Puente SYNC a la BD (Lakebase) para scripts (audit, migrate --apply, backfills, fixes).

Los ~10 scripts sync del repo se conectan a la BD vía `get_sync_db()`, un
wrapper sync PEREZOSO sobre el adaptador async (event loop dedicado en un thread
de fondo). Perezoso porque varios scripts construyen el handle a nivel de módulo
(import) y recién deben conectar al primer uso.

Superficie soportada (la que usan los scripts): acceso por índice Y por
atributo (`db["views"]` / `db.views`), find (iterable, con sort/limit/skip),
find_one, insert/update/replace/delete, distinct, count_documents, aggregate,
bulk_write, drop, create_index, list_collection_names, command.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

_loop: asyncio.AbstractEventLoop | None = None
_db: "SyncDatabase | None" = None
_lock = threading.Lock()


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


def get_sync_db():
    """Handle sync de la base (Lakebase; ver docstring del módulo)."""
    global _db
    if _db is None:
        _db = SyncDatabase()
    return _db


class SyncDatabase:
    """Wrapper sync PEREZOSO del LakebaseDatabase (conecta al primer uso)."""

    def __init__(self) -> None:
        self._adb: Any | None = None
        self._connect_lock = threading.Lock()

    def _handle(self) -> Any:
        if self._adb is None:
            with self._connect_lock:
                if self._adb is None:
                    from app.core.db import client as db_client

                    async def _connect():
                        await db_client.connect()
                        return await db_client.get_db()

                    self._adb = _run(_connect())
        return self._adb

    def __getitem__(self, name: str) -> "SyncCollection":
        return SyncCollection(self, name)

    def __getattr__(self, name: str) -> "SyncCollection":
        if name.startswith("_"):
            raise AttributeError(name)
        return SyncCollection(self, name)

    def list_collection_names(self) -> list[str]:
        return _run(self._handle().list_collection_names())

    def command(self, cmd: Any) -> dict:
        return _run(self._handle().command(cmd))


class SyncCursor:
    def __init__(self, acursor: Any) -> None:
        self._c = acursor

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
        return iter(_run(self._c.to_list(None)))


class SyncCollection:
    def __init__(self, sdb: SyncDatabase, name: str) -> None:
        self._sdb = sdb
        self._name = name

    @property
    def _a(self) -> Any:
        return self._sdb._handle()[self._name]

    def find(self, *args: Any, **kwargs: Any) -> SyncCursor:
        return SyncCursor(self._a.find(*args, **kwargs))

    def aggregate(self, pipeline: list[dict], **kwargs: Any) -> list[dict]:
        return _run(self._a.aggregate(pipeline, **kwargs).to_list(None))

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        # Métodos async 1:1 (find_one, insert_one, update_one, delete_many,
        # distinct, count_documents, bulk_write, drop, create_index, …).
        target = getattr(self._a, name)

        def call(*args: Any, **kwargs: Any) -> Any:
            return _run(target(*args, **kwargs))

        return call
