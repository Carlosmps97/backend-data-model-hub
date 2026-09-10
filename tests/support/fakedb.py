"""BD en memoria con la superficie ASYNC estilo pymongo que usan los repositorios.

Envuelve `mongomock` (semántica Mongo real: filtros `$in/$regex/$or/$ne/$exists`,
proyecciones, operadores de update, `bulk_write`, `aggregate`) con la fachada
async del adaptador Lakebase (`app/core/db/lakebase/collection.py`): cursores
con `.sort/.skip/.limit/.to_list`, métodos awaitables y el dialecto propio
`$mergeObjects` (que el adaptador implementa en SQL y mongomock no conoce).

NO sustituye a la suite viva del adaptador (`LAKEBASE_TESTS=1`, que prueba el
SQL real): prueba el CABLEADO — routers → services → repositorios → filtros —
con las firmas y los guards REALES. Es exactamente la capa que la suite de
unit tests (repos mockeados) no puede ver, y donde vivieron los 500 del doc 80.
"""
from __future__ import annotations

from typing import Any

import mongomock
from pymongo import ReturnDocument


class FakeCursor:
    """Espejo de `PgCursor`: encadenable y awaitable en `to_list`."""

    def __init__(self, cursor) -> None:
        self._c = cursor

    def sort(self, key_or_list, direction: int | None = None) -> "FakeCursor":
        if isinstance(key_or_list, str):
            self._c = self._c.sort(key_or_list, 1 if direction is None else direction)
        else:
            self._c = self._c.sort(key_or_list)
        return self

    def skip(self, n: int) -> "FakeCursor":
        self._c = self._c.skip(n)
        return self

    def limit(self, n: int) -> "FakeCursor":
        self._c = self._c.limit(n)
        return self

    def max_time_ms(self, ms: int) -> "FakeCursor":
        return self

    async def to_list(self, length: int | None = None) -> list[dict]:
        docs = list(self._c)
        return docs if length is None else docs[:length]

    def __aiter__(self):
        async def _gen():
            for d in list(self._c):
                yield d
        return _gen()


class _BulkResult:
    def __init__(self, matched: int, modified: int, deleted: int, inserted: int) -> None:
        self.matched_count, self.modified_count = matched, modified
        self.deleted_count, self.inserted_count = deleted, inserted
        self.upserted_count = 0


class FakeCollection:
    """Espejo de `PgCollection` sobre una colección mongomock."""

    def __init__(self, coll) -> None:
        self._c = coll
        self.name = coll.name

    # ── lectura ──────────────────────────────────────────────────────────
    def find(self, filter: dict | None = None, projection: dict | None = None) -> FakeCursor:
        return FakeCursor(self._c.find(filter or {}, projection))

    async def find_one(self, filter: dict | None = None, projection: dict | None = None) -> dict | None:
        return self._c.find_one(filter or {}, projection)

    async def count_documents(self, filter: dict | None = None, maxTimeMS: int | None = None) -> int:
        return self._c.count_documents(filter or {})

    async def distinct(self, key: str, filter: dict | None = None) -> list:
        return self._c.distinct(key, filter or {})

    def aggregate(self, pipeline: list[dict], maxTimeMS: int | None = None, **_: Any) -> FakeCursor:
        return FakeCursor(self._c.aggregate(pipeline))

    # ── escritura ────────────────────────────────────────────────────────
    async def insert_one(self, doc: dict):
        return self._c.insert_one(dict(doc))

    async def insert_many(self, docs: list[dict], ordered: bool = True):
        return self._c.insert_many([dict(d) for d in docs], ordered=ordered)

    async def update_one(self, filter: dict, update: dict, upsert: bool = False):
        return self._c.update_one(filter, self._rewrite(filter, update), upsert=upsert)

    async def update_many(self, filter: dict, update: dict):
        return self._c.update_many(filter, update)

    async def replace_one(self, filter: dict, doc: dict, upsert: bool = False):
        return self._c.replace_one(filter, dict(doc), upsert=upsert)

    async def find_one_and_update(self, filter: dict, update: dict, *, return_document: bool = False,
                                  projection: dict | None = None, upsert: bool = False, **_: Any):
        return self._c.find_one_and_update(
            filter, self._rewrite(filter, update), projection=projection, upsert=upsert,
            return_document=ReturnDocument.AFTER if return_document else ReturnDocument.BEFORE)

    async def delete_one(self, filter: dict):
        return self._c.delete_one(filter)

    async def delete_many(self, filter: dict):
        return self._c.delete_many(filter)

    async def bulk_write(self, ops: list, ordered: bool = True):
        """Despacha los ops de pymongo uno a uno (el `bulk_write` de mongomock
        no acepta los objetos de pymongo ≥ 4.10). Misma semántica por-op."""
        from pymongo import DeleteMany, DeleteOne, InsertOne, ReplaceOne, UpdateMany, UpdateOne
        matched = modified = deleted = inserted = 0
        for op in ops:
            if isinstance(op, InsertOne):
                self._c.insert_one(dict(op._doc)); inserted += 1
            elif isinstance(op, ReplaceOne):
                r = self._c.replace_one(op._filter, dict(op._doc), upsert=op._upsert)
                matched += r.matched_count; modified += r.modified_count
            elif isinstance(op, UpdateOne):
                r = self._c.update_one(op._filter, self._rewrite(op._filter, op._doc), upsert=op._upsert)
                matched += r.matched_count; modified += r.modified_count
            elif isinstance(op, UpdateMany):
                r = self._c.update_many(op._filter, op._doc, upsert=op._upsert)
                matched += r.matched_count; modified += r.modified_count
            elif isinstance(op, DeleteOne):
                deleted += self._c.delete_one(op._filter).deleted_count
            elif isinstance(op, DeleteMany):
                deleted += self._c.delete_many(op._filter).deleted_count
            else:  # pragma: no cover
                raise TypeError(f"op no soportado en el fake: {type(op).__name__}")
        return _BulkResult(matched, modified, deleted, inserted)

    # ── DDL ──────────────────────────────────────────────────────────────
    async def create_index(self, keys, unique: bool = False, **_: Any) -> str:
        try:
            return self._c.create_index(keys, unique=unique)
        except Exception:  # noqa: BLE001 — wildcard `$**`: mongomock no lo modela
            return "ix_fake"

    async def drop_index(self, idx_name: str) -> None:
        try:
            self._c.drop_index(idx_name)
        except Exception:  # noqa: BLE001
            pass

    async def drop(self) -> None:
        self._c.drop()

    def _rewrite(self, filter: dict, update: dict) -> dict:
        """`$mergeObjects` (dialecto del adaptador, ver translate.update_expr):
        mergea un objeto top-level SIN pasar por dot-paths — las keys pueden
        traer puntos (usernames SSO = correos). Se traduce a un `$set` del
        objeto ya mergeado con el doc actual."""
        merge = update.get("$mergeObjects")
        if not merge:
            return update
        current = self._c.find_one(filter) or {}
        merged = {field: {**(current.get(field) or {}), **obj} for field, obj in merge.items()}
        rest = {k: v for k, v in update.items() if k != "$mergeObjects"}
        return {**rest, "$set": {**(rest.get("$set") or {}), **merged}}


class FakeDb:
    """Espejo de `LakebaseDatabase`: `db[coll]`, `command`, `list_collection_names`."""

    def __init__(self) -> None:
        self._client = mongomock.MongoClient()
        self.raw = self._client["dmh"]          # acceso SYNC para sembrar/inspeccionar en tests

    def __getitem__(self, name: str) -> FakeCollection:
        return FakeCollection(self.raw[name])

    async def command(self, cmd: Any) -> dict:
        return {"ok": 1}

    async def list_collection_names(self) -> list[str]:
        return self.raw.list_collection_names()
