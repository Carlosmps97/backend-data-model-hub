"""`LakebaseDatabase` / `PgCollection`: la superficie pymongo sobre Postgres.

Cada "colección" es una tabla `(id text PRIMARY KEY, doc jsonb)` con un índice
GIN `jsonb_path_ops` (todas las igualdades/jsonpath del traductor). Los docs se
guardan COMPLETOS, `_id` incluido, así el camino de lectura de los repositorios
es idéntico al de pymongo.

Atomicidad: los `find_one_and_update`/`update_one`/`replace_one`/`delete_one`
restringen a UNA fila con `WITH target … LIMIT 1 FOR UPDATE` (equivalente al
"primera que matchee" de Mongo, con lock de fila). Violación de único →
`pymongo.errors.DuplicateKeyError`, para que los retries existentes del código
(seq de standards_versions) funcionen sin cambios.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import asyncpg
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.core.db.lakebase.aggregate import compile_pipeline
from app.core.db.lakebase.translate import (
    Sql,
    _json_default,
    filter_sql,
    is_update_doc,
    order_by_find,
    projection_expr,
    update_expr,
    upsert_doc,
)

log = logging.getLogger(__name__)


def _dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=_json_default)


def _loads(value: Any) -> Any:
    """asyncpg devuelve jsonb como texto (sin codec custom, a propósito)."""
    import json

    return json.loads(value) if isinstance(value, str) else value

_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")

# Las 19 colecciones propias + column_catalog (del servicio de agentes; se
# preserva). Se pre-crean al conectar; cualquier otra se crea on-demand.
KNOWN_COLLECTIONS = [
    "projects", "folders", "subject_areas", "schemas",
    "canonical_tables", "canonical_columns", "relationships", "views",
    "changesets", "changeset_changes", "standards_versions",
    "parent_domains", "glossary_terms", "udp_definitions", "naming_config",
    "users", "roles", "saved_reports", "audit_log",
    "column_catalog",
]


def _check_name(name: str) -> str:
    if not _NAME_RE.match(name):
        raise ValueError(f"Nombre de colección inválido: {name!r}")
    return name


class _WriteResult:
    """Resultado mínimo compatible (inserted/matched/modified/…)."""

    def __init__(self, *, inserted_id=None, matched=0, modified=0, deleted=0,
                 upserted_id=None, inserted_ids=None) -> None:
        self.acknowledged = True
        self.inserted_id = inserted_id
        self.inserted_ids = inserted_ids or []
        self.matched_count = matched
        self.modified_count = modified
        self.deleted_count = deleted
        self.upserted_id = upserted_id
        # Compat BulkWriteResult
        self.inserted_count = len(self.inserted_ids)
        self.upserted_count = 1 if upserted_id is not None else 0
        self.bulk_api_result: dict = {}


def _rowcount(status: str) -> int:
    try:
        return int(status.rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


def _apply_projection(doc: dict, projection: dict | None) -> dict:
    if not projection or doc is None:
        return doc
    keys = {k: v for k, v in projection.items() if k != "_id"}
    include_mode = any(bool(v) for v in keys.values())
    if include_mode:
        out = {k: doc[k] for k in keys if k in doc}
        if projection.get("_id", 1) and "_id" in doc:
            out["_id"] = doc["_id"]
        return out
    out = {k: v for k, v in doc.items() if k not in keys}
    if not projection.get("_id", 1):
        out.pop("_id", None)
    return out


def _new_object_id() -> str:
    return str(ObjectId())


class PgCursor:
    """Cursor de find: encadena sort/skip/limit/max_time_ms y bufferiza."""

    def __init__(self, coll: "PgCollection", flt: dict | None, projection: dict | None) -> None:
        self._coll = coll
        self._filter = flt or {}
        self._projection = projection
        self._sort: list[tuple[str, int]] = []
        self._skip_n: int | None = None
        self._limit_n: int | None = None
        self._timeout: float | None = None
        self._buffer: list[dict] | None = None

    def sort(self, key_or_list, direction: int | None = None) -> "PgCursor":
        if isinstance(key_or_list, str):
            self._sort = [(key_or_list, 1 if direction is None else int(direction))]
        else:
            self._sort = [(k, int(d)) for k, d in key_or_list]
        return self

    def skip(self, n: int) -> "PgCursor":
        self._skip_n = int(n)
        return self

    def limit(self, n: int) -> "PgCursor":
        self._limit_n = int(n)
        return self

    def max_time_ms(self, ms: int) -> "PgCursor":
        self._timeout = ms / 1000.0
        return self

    async def _fetch(self) -> list[dict]:
        if self._buffer is None:
            self._buffer = await self._coll._run_find(
                self._filter, self._projection, self._sort,
                self._skip_n, self._limit_n, self._timeout,
            )
        return self._buffer

    async def to_list(self, length: int | None = None) -> list[dict]:
        docs = await self._fetch()
        return docs[:length] if length is not None else list(docs)

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for doc in await self._fetch():
            yield doc


class PgCommandCursor:
    """Cursor de aggregate (bufferizado)."""

    def __init__(self, coll: "PgCollection", pipeline: list[dict], timeout: float | None) -> None:
        self._coll = coll
        self._pipeline = pipeline
        self._timeout = timeout
        self._buffer: list[dict] | None = None

    async def _fetch(self) -> list[dict]:
        if self._buffer is None:
            self._buffer = await self._coll._run_aggregate(self._pipeline, self._timeout)
        return self._buffer

    async def to_list(self, length: int | None = None) -> list[dict]:
        docs = await self._fetch()
        return docs[:length] if length is not None else list(docs)

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for doc in await self._fetch():
            yield doc


class PgCollection:
    def __init__(self, db: "LakebaseDatabase", name: str) -> None:
        self.database = db
        self.name = _check_name(name)

    # ─── infra ──────────────────────────────────────────────────────

    @property
    def _table(self) -> str:
        return f'"{self.database.schema}"."{self.name}"'

    async def _conn(self):
        await self.database.ensure_table(self.name)
        return self.database.pool.acquire()

    # ─── lecturas ───────────────────────────────────────────────────

    def find(self, filter: dict | None = None, projection: dict | None = None) -> PgCursor:
        return PgCursor(self, filter, projection)

    async def find_one(self, filter: dict | None = None, projection: dict | None = None) -> dict | None:
        docs = await self.find(filter, projection).limit(1).to_list(1)
        return docs[0] if docs else None

    async def _run_find(self, flt, projection, sort, skip_n, limit_n, timeout) -> list[dict]:
        s = Sql()
        proj = projection_expr(projection, s)
        where = filter_sql(flt, s, doc="doc", mode="table")
        order = order_by_find(sort, s) if sort else ""
        sql = f"SELECT {proj} AS doc FROM {self._table} WHERE {where} {order}"
        if limit_n is not None:
            sql += f" LIMIT {s.add(limit_n)}"
        if skip_n is not None:
            sql += f" OFFSET {s.add(skip_n)}"
        async with await self._conn() as conn:
            rows = await conn.fetch(sql, *s.params, timeout=timeout)
        return [_loads(r["doc"]) for r in rows]

    async def count_documents(self, filter: dict | None = None, maxTimeMS: int | None = None) -> int:
        s = Sql()
        where = filter_sql(filter, s, doc="doc", mode="table")
        async with await self._conn() as conn:
            row = await conn.fetchrow(
                f"SELECT count(*) AS n FROM {self._table} WHERE {where}",
                *s.params, timeout=(maxTimeMS / 1000.0 if maxTimeMS else None),
            )
        return int(row["n"])

    async def distinct(self, key: str, filter: dict | None = None) -> list:
        s = Sql()
        where = filter_sql(filter, s, doc="doc", mode="table")
        path = "$" + "".join(f'."{seg}"' for seg in key.split(".")) + "[*]"
        sql = (
            f"SELECT DISTINCT v.val AS val FROM {self._table}, "
            f"LATERAL jsonb_path_query(doc, {s.add(path)}::jsonpath) AS v(val) "
            f"WHERE {where}"
        )
        async with await self._conn() as conn:
            rows = await conn.fetch(sql, *s.params)
        return [_loads(r["val"]) for r in rows]

    def aggregate(self, pipeline: list[dict], maxTimeMS: int | None = None, **_: Any) -> PgCommandCursor:
        return PgCommandCursor(self, pipeline, maxTimeMS / 1000.0 if maxTimeMS else None)

    async def _run_aggregate(self, pipeline: list[dict], timeout: float | None) -> list[dict]:
        s = Sql()
        sql = compile_pipeline(pipeline, self._table, s)
        async with await self._conn() as conn:
            rows = await conn.fetch(sql, *s.params, timeout=timeout)
        return [_loads(r["d"]) for r in rows]

    # ─── escrituras ─────────────────────────────────────────────────

    async def insert_one(self, doc: dict) -> _WriteResult:
        doc = dict(doc)
        _id = doc.get("_id")
        if _id is None:
            _id = _new_object_id()
        doc["_id"] = _id if isinstance(_id, str) else str(_id)
        s = Sql()
        sql = f"INSERT INTO {self._table} (id, doc) VALUES ({s.add(doc['_id'])}, {s.add_json(doc)})"
        try:
            async with await self._conn() as conn:
                await conn.execute(sql, *s.params)
        except asyncpg.exceptions.UniqueViolationError as exc:
            raise DuplicateKeyError(str(exc)) from exc
        return _WriteResult(inserted_id=doc["_id"])

    async def insert_many(self, docs: list[dict], ordered: bool = True) -> _WriteResult:
        rows: list[tuple[str, str]] = []
        ids: list[str] = []
        for d in docs:
            d = dict(d)
            _id = d.get("_id") or _new_object_id()
            d["_id"] = _id if isinstance(_id, str) else str(_id)
            ids.append(d["_id"])
            rows.append((d["_id"], _dumps(d)))
        try:
            async with await self._conn() as conn:
                await conn.executemany(
                    f"INSERT INTO {self._table} (id, doc) VALUES ($1, $2::jsonb)", rows
                )
        except asyncpg.exceptions.UniqueViolationError as exc:
            raise DuplicateKeyError(str(exc)) from exc
        return _WriteResult(inserted_ids=ids)

    async def update_one(self, filter: dict, update: dict, upsert: bool = False) -> _WriteResult:
        if not is_update_doc(update):
            return await self.replace_one(filter, update, upsert=upsert)
        for _ in range(2):
            s = Sql()
            expr = update_expr(update, s)
            where = filter_sql(filter, s, doc="doc", mode="table")
            sql = (
                f"WITH target AS (SELECT id FROM {self._table} WHERE {where} "
                f"LIMIT 1 FOR UPDATE) "
                f"UPDATE {self._table} AS u SET doc = {expr} "
                f"FROM target WHERE u.id = target.id"
            )
            async with await self._conn() as conn:
                status = await conn.execute(sql, *s.params)
            n = _rowcount(status)
            if n or not upsert:
                return _WriteResult(matched=n, modified=n)
            inserted = await self._try_upsert_insert(filter, update)
            if inserted is not None:
                return _WriteResult(upserted_id=inserted)
        raise DuplicateKeyError(f"upsert en {self.name}: colisión persistente")

    async def update_many(self, filter: dict, update: dict) -> _WriteResult:
        s = Sql()
        expr = update_expr(update, s)
        where = filter_sql(filter, s, doc="doc", mode="table")
        async with await self._conn() as conn:
            status = await conn.execute(
                f"UPDATE {self._table} SET doc = {expr} WHERE {where}", *s.params
            )
        n = _rowcount(status)
        return _WriteResult(matched=n, modified=n)

    async def replace_one(self, filter: dict, doc: dict, upsert: bool = False) -> _WriteResult:
        doc = {k: v for k, v in doc.items()}
        for _ in range(2):
            s = Sql()
            where = filter_sql(filter, s, doc="doc", mode="table")
            newdoc = s.add_json(doc)
            sql = (
                f"WITH target AS (SELECT id FROM {self._table} WHERE {where} "
                f"LIMIT 1 FOR UPDATE) "
                f"UPDATE {self._table} AS u SET doc = jsonb_set({newdoc}, '{{_id}}', to_jsonb(u.id), true) "
                f"FROM target WHERE u.id = target.id"
            )
            async with await self._conn() as conn:
                status = await conn.execute(sql, *s.params)
            n = _rowcount(status)
            if n or not upsert:
                return _WriteResult(matched=n, modified=n)
            _id = doc.get("_id") or (filter or {}).get("_id")
            if not isinstance(_id, str):
                _id = _new_object_id() if _id is None else str(_id)
            ins = {**doc, "_id": _id}
            inserted = await self._insert_if_absent(_id, ins)
            if inserted:
                return _WriteResult(upserted_id=_id)
        raise DuplicateKeyError(f"replace upsert en {self.name}: colisión persistente")

    async def _try_upsert_insert(self, filter: dict, update: dict) -> str | None:
        """Branch INSERT de un upsert: doc = igualdades del filtro + $set +
        $setOnInsert. Devuelve el _id insertado o None si otro proceso ganó."""
        base = upsert_doc(filter, update)
        _id = base.get("_id")
        if _id is None:
            _id = _new_object_id()
        base["_id"] = _id if isinstance(_id, str) else str(_id)
        inserted = await self._insert_if_absent(base["_id"], base)
        return base["_id"] if inserted else None

    async def _insert_if_absent(self, _id: str, doc: dict) -> bool:
        s = Sql()
        sql = (
            f"INSERT INTO {self._table} (id, doc) VALUES ({s.add(_id)}, {s.add_json(doc)}) "
            f"ON CONFLICT (id) DO NOTHING"
        )
        try:
            async with await self._conn() as conn:
                status = await conn.execute(sql, *s.params)
        except asyncpg.exceptions.UniqueViolationError as exc:
            # Índice único de EXPRESIÓN (p. ej. seq): el ON CONFLICT (id) no lo
            # cubre → mismo contrato que Mongo.
            raise DuplicateKeyError(str(exc)) from exc
        return _rowcount(status) == 1

    async def find_one_and_update(
        self, filter: dict, update: dict, *, return_document: bool = False,
        projection: dict | None = None, upsert: bool = False, **_: Any,
    ) -> dict | None:
        # return_document: pymongo.ReturnDocument.AFTER == True
        for _ in range(2):
            s = Sql()
            expr = update_expr(update, s)
            where = filter_sql(filter, s, doc="doc", mode="table")
            sql = (
                f"WITH target AS (SELECT id, doc AS old FROM {self._table} WHERE {where} "
                f"LIMIT 1 FOR UPDATE) "
                f"UPDATE {self._table} AS u SET doc = {expr} "
                f"FROM target WHERE u.id = target.id "
                f"RETURNING u.doc AS newdoc, target.old AS olddoc"
            )
            async with await self._conn() as conn:
                row = await conn.fetchrow(sql, *s.params)
            if row is not None:
                doc = _loads(row["newdoc"] if return_document else row["olddoc"])
                return _apply_projection(doc, projection)
            if not upsert:
                return None
            inserted = await self._try_upsert_insert(filter, update)
            if inserted is not None:
                if not return_document:
                    return None
                return _apply_projection(await self.find_one({"_id": inserted}), projection)
        raise DuplicateKeyError(f"find_one_and_update upsert en {self.name}: colisión persistente")

    async def delete_one(self, filter: dict) -> _WriteResult:
        s = Sql()
        where = filter_sql(filter, s, doc="doc", mode="table")
        sql = (
            f"DELETE FROM {self._table} WHERE id IN "
            f"(SELECT id FROM {self._table} WHERE {where} LIMIT 1)"
        )
        async with await self._conn() as conn:
            status = await conn.execute(sql, *s.params)
        return _WriteResult(deleted=_rowcount(status))

    async def delete_many(self, filter: dict) -> _WriteResult:
        s = Sql()
        where = filter_sql(filter, s, doc="doc", mode="table")
        async with await self._conn() as conn:
            status = await conn.execute(f"DELETE FROM {self._table} WHERE {where}", *s.params)
        return _WriteResult(deleted=_rowcount(status))

    # ─── bulk ───────────────────────────────────────────────────────

    async def bulk_write(self, ops: list, ordered: bool = True) -> _WriteResult:
        fast = self._bulk_fast_path(ops)
        if fast is not None:
            return await fast
        total = _WriteResult()
        for op in ops:
            kind = type(op).__name__
            if kind == "UpdateOne":
                r = await self.update_one(op._filter, op._doc, upsert=bool(getattr(op, "_upsert", False)))
            elif kind == "UpdateMany":
                r = await self.update_many(op._filter, op._doc)
            elif kind == "ReplaceOne":
                r = await self.replace_one(op._filter, op._doc, upsert=bool(getattr(op, "_upsert", False)))
            elif kind == "InsertOne":
                r = await self.insert_one(op._doc)
            elif kind == "DeleteOne":
                r = await self.delete_one(op._filter)
            elif kind == "DeleteMany":
                r = await self.delete_many(op._filter)
            else:
                raise NotImplementedError(f"bulk_write: operación no soportada {kind}")
            total.matched_count += r.matched_count
            total.modified_count += r.modified_count
            total.deleted_count += r.deleted_count
            total.upserted_count += r.upserted_count
        return total

    def _bulk_fast_path(self, ops: list):
        """Lotes homogéneos por `_id` (seeds/backfills/apply del changeset) en
        1-2 round-trips en vez de N:
        - ReplaceOne({_id}, doc, upsert) → INSERT … ON CONFLICT DO UPDATE.
        - UpdateOne({_id}, {$set[, $setOnInsert]}[, upsert]) con paths simples
          → UPDATE … FROM unnest + INSERT faltantes.
        """
        if not ops:
            return None
        kinds = {type(op).__name__ for op in ops}
        def _id_only(op) -> bool:
            f = op._filter
            return isinstance(f, dict) and list(f) == ["_id"] and isinstance(f["_id"], str)
        if kinds == {"ReplaceOne"} and all(_id_only(op) for op in ops):
            return self._bulk_replace_by_id(ops)
        if kinds == {"UpdateOne"} and all(
            _id_only(op)
            and set(op._doc) <= {"$set", "$setOnInsert"}
            and all("." not in k for k in (op._doc.get("$set") or {}))
            and all("." not in k for k in (op._doc.get("$setOnInsert") or {}))
            for op in ops
        ):
            return self._bulk_update_by_id(ops)
        return None

    async def _bulk_replace_by_id(self, ops: list) -> _WriteResult:
        ids, docs = [], []
        for op in ops:
            _id = op._filter["_id"]
            ids.append(_id)
            docs.append(_dumps({**op._doc, "_id": _id}))
        upsert = all(bool(getattr(op, "_upsert", False)) for op in ops)
        if upsert:
            sql = (
                f"INSERT INTO {self._table} (id, doc) "
                f"SELECT u.uid, u.udoc::jsonb FROM unnest($1::text[], $2::text[]) AS u(uid, udoc) "
                f"ON CONFLICT (id) DO UPDATE SET doc = EXCLUDED.doc"
            )
        else:
            sql = (
                f"UPDATE {self._table} AS t SET doc = u.udoc::jsonb "
                f"FROM unnest($1::text[], $2::text[]) AS u(uid, udoc) WHERE t.id = u.uid"
            )
        async with await self._conn() as conn:
            status = await conn.execute(sql, ids, docs)
        return _WriteResult(matched=_rowcount(status), modified=_rowcount(status))

    async def _bulk_update_by_id(self, ops: list) -> _WriteResult:
        ids, patches, inserts = [], [], []
        any_upsert = False
        for op in ops:
            _id = op._filter["_id"]
            set_fields = op._doc.get("$set") or {}
            ids.append(_id)
            patches.append(_dumps(set_fields))
            base = {"_id": _id, **(op._doc.get("$setOnInsert") or {}), **set_fields}
            inserts.append(_dumps(base))
            any_upsert = any_upsert or bool(getattr(op, "_upsert", False))
        async with await self._conn() as conn:
            async with conn.transaction():
                status = await conn.execute(
                    f"UPDATE {self._table} AS t SET doc = t.doc || u.patch::jsonb "
                    f"FROM unnest($1::text[], $2::text[]) AS u(uid, patch) WHERE t.id = u.uid",
                    ids, patches,
                )
                if any_upsert:
                    await conn.execute(
                        f"INSERT INTO {self._table} (id, doc) "
                        f"SELECT u.uid, u.udoc::jsonb FROM unnest($1::text[], $2::text[]) AS u(uid, udoc) "
                        f"ON CONFLICT (id) DO NOTHING",
                        ids, inserts,
                    )
        n = _rowcount(status)
        return _WriteResult(matched=n, modified=n)

    # ─── DDL ────────────────────────────────────────────────────────

    async def create_index(self, keys, unique: bool = False, **_: Any) -> str:
        if isinstance(keys, str):
            keys = [(keys, 1)]
        fields = [(str(f), int(d)) for f, d in keys]
        if any(f.endswith(".$**") for f, _ in fields):
            return f"gin_{self.name}"  # el GIN de la tabla cubre los wildcard
        return await self.database.create_field_index(self.name, fields, unique=unique)

    async def drop(self) -> None:
        await self.database.drop_table(self.name)


class LakebaseDatabase:
    """Handle de BD compatible con la superficie usada de AsyncIOMotorDatabase."""

    def __init__(self, pool: asyncpg.Pool, schema: str) -> None:
        self.pool = pool
        self.schema = _check_name(schema)
        self._ensured: set[str] = set()
        self._known_indexes: set[str] = set()
        self._ddl_lock = asyncio.Lock()

    def __getitem__(self, name: str) -> PgCollection:
        return PgCollection(self, name)

    async def command(self, cmd: Any) -> dict:
        if cmd == "ping" or (isinstance(cmd, dict) and "ping" in cmd):
            async with self.pool.acquire() as conn:
                await conn.execute("SELECT 1")
            return {"ok": 1}
        raise NotImplementedError(f"db.command({cmd!r}) no soportado")

    async def list_collection_names(self) -> list[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = $1", self.schema
            )
        return [r["tablename"] for r in rows]

    async def ensure_base(self) -> None:
        """Censa el schema y emite SOLO el DDL faltante, en UN round-trip
        (multi-statement, sin params): ~110 statements secuenciales sobre WAN
        costaban ~30 s por reload.

        Censar primero es CLAVE en Databricks Apps: el service principal de la
        app no es owner de las tablas ya existentes, y en Postgres
        hasta un `CREATE INDEX IF NOT EXISTS` de un índice YA existente falla
        con "must be owner of table ..." (el chequeo de ownership corre antes
        del IF NOT EXISTS; visto en el primer arranque en Apps 2026-07-20).
        En operación normal (todo migrado) aquí no se ejecuta ningún DDL."""
        async with self._ddl_lock:
            async with self.pool.acquire() as conn:
                has_schema = await conn.fetchrow(
                    "SELECT 1 FROM pg_namespace WHERE nspname = $1", self.schema
                )
                tables = {
                    r["tablename"]
                    for r in await conn.fetch(
                        "SELECT tablename FROM pg_tables WHERE schemaname = $1", self.schema
                    )
                }
                indexes = {
                    r["indexname"]
                    for r in await conn.fetch(
                        "SELECT indexname FROM pg_indexes WHERE schemaname = $1", self.schema
                    )
                }
                stmts: list[str] = []
                if has_schema is None:
                    stmts.append(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
                for name in KNOWN_COLLECTIONS:
                    if name not in tables:
                        stmts.append(
                            f'CREATE TABLE IF NOT EXISTS "{self.schema}"."{name}" '
                            f"(id text PRIMARY KEY, doc jsonb NOT NULL)"
                        )
                    if f"gin_{name}" not in indexes:
                        stmts.append(
                            f'CREATE INDEX IF NOT EXISTS "gin_{name}" ON "{self.schema}"."{name}" '
                            f"USING gin (doc jsonb_path_ops)"
                        )
                if stmts:
                    await conn.execute(";\n".join(stmts))
                    indexes = {
                        r["indexname"]
                        for r in await conn.fetch(
                            "SELECT indexname FROM pg_indexes WHERE schemaname = $1",
                            self.schema,
                        )
                    }
            self._ensured.update(KNOWN_COLLECTIONS)
            self._known_indexes = indexes

    async def ensure_table(self, name: str) -> None:
        if name in self._ensured:
            return
        _check_name(name)
        async with self._ddl_lock:
            if name in self._ensured:
                return
            async with self.pool.acquire() as conn:
                # Igual que ensure_base: DDL solo si FALTA (en Apps el SP no es
                # owner de tablas pre-existentes y el CREATE fallaría).
                exists = await conn.fetchrow(
                    "SELECT 1 FROM pg_tables WHERE schemaname = $1 AND tablename = $2",
                    self.schema,
                    name,
                )
                has_gin = await conn.fetchrow(
                    "SELECT 1 FROM pg_indexes WHERE schemaname = $1 AND indexname = $2",
                    self.schema,
                    f"gin_{name}",
                )
                if exists is None:
                    await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
                    await conn.execute(
                        f'CREATE TABLE IF NOT EXISTS "{self.schema}"."{name}" '
                        f"(id text PRIMARY KEY, doc jsonb NOT NULL)"
                    )
                if has_gin is None:
                    await conn.execute(
                        f'CREATE INDEX IF NOT EXISTS "gin_{name}" ON "{self.schema}"."{name}" '
                        f"USING gin (doc jsonb_path_ops)"
                    )
            self._ensured.add(name)

    async def create_field_index(
        self, table: str, fields: list[tuple[str, int]], unique: bool = False
    ) -> str:
        await self.ensure_table(table)
        exprs: list[str] = []
        name_parts: list[str] = []
        for field, direction in fields:
            if not re.match(r"^[A-Za-z0-9_.]+$", field):
                raise ValueError(f"Campo de índice inválido: {field!r}")
            segs = field.split(".")
            if len(segs) == 1:
                extract = f"(doc ->> '{field}')"
            else:
                extract = "(doc #>> '{" + ",".join(segs) + "}')"
            exprs.append(f'({extract} COLLATE "C") {"ASC" if direction >= 0 else "DESC"}')
            name_parts.append(re.sub(r"[^A-Za-z0-9]+", "_", field) + ("_d" if direction < 0 else ""))
        idx_name = f"ix_{table}_" + "_".join(name_parts)
        idx_name = idx_name[:63]
        if idx_name in self._known_indexes:
            return idx_name  # ya existe (censo de ensure_base): 0 round-trips
        uniq = "UNIQUE " if unique else ""
        async with self._ddl_lock:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    f'CREATE {uniq}INDEX IF NOT EXISTS "{idx_name}" '
                    f'ON "{self.schema}"."{table}" ({", ".join(exprs)})'
                )
            self._known_indexes.add(idx_name)
        return idx_name

    async def drop_table(self, name: str) -> None:
        _check_name(name)
        async with self._ddl_lock:
            async with self.pool.acquire() as conn:
                await conn.execute(f'DROP TABLE IF EXISTS "{self.schema}"."{name}"')
            self._ensured.discard(name)
