"""FakeDb que además GENERA el SQL de Lakebase de cada lectura (doc 105, ronda 3).

Adaptado del `sqlcheck.py` del revisor R5. Responde con mongomock (como
`FakeDb`) y, en cada `find`/`count_documents`/`aggregate`, arma el SQL con el
traductor REAL del adaptador (`filter_sql`, `projection_expr`, `order_by_find`,
`compile_pipeline`) y anota en `db.problems` lo que Postgres/asyncpg
rechazarían: una excepción del traductor (NotImplementedError → 500), texto con
NUL o surrogates sueltos, un jsonpath/jsonb con `\\u0000` o con NaN/Infinity, y
enteros de LIMIT/OFFSET fuera de int8. FakeDb sola traga todo eso."""
from __future__ import annotations

import re
from typing import Any

from app.core.db.lakebase.aggregate import compile_pipeline
from app.core.db.lakebase.translate import Sql, filter_sql, order_by_find, projection_expr
from tests.support.fakedb import FakeCollection, FakeCursor, FakeDb

_BAD_JP_NUM = re.compile(r"(?<![\w\"])(NaN|-?Infinity)(?![\w\"])")


def _real_nul_escape(v: str) -> bool:
    """¿`\\u0000` precedido por un número IMPAR de barras (escape real)?"""
    i = v.find("u0000")
    while i != -1:
        j, bars = i - 1, 0
        while j >= 0 and v[j] == "\\":
            bars += 1
            j -= 1
        if bars % 2 == 1:
            return True
        i = v.find("u0000", i + 1)
    return False


def _check(problems: list[str], sql: str, params: list) -> None:
    for i, p in enumerate(params, 1):
        cast = re.search(rf"\${i}(?!\d)(::\w+)?", sql)
        kind = cast.group(1) if cast else None
        for v in (p if isinstance(p, list) else [p]):
            if isinstance(v, str):
                if "\x00" in v:
                    problems.append(f"param ${i} con NUL")
                try:
                    v.encode("utf-8")
                except UnicodeEncodeError:
                    problems.append(f"param ${i} con surrogate suelto")
                if kind in ("::jsonpath", "::jsonb") and (_real_nul_escape(v) or _BAD_JP_NUM.search(v)):
                    problems.append(f"{kind} ${i} inválido para Postgres: {v[:80]}")
            if isinstance(v, int) and not isinstance(v, bool) and not (-2 ** 63 <= v < 2 ** 63):
                problems.append(f"param ${i} entero fuera de int8")


def _translate(problems: list[str], build) -> None:
    try:
        sql, params = build()
    except Exception as e:  # noqa: BLE001 — cualquier excepción del traductor es un 500 en Lakebase
        problems.append(f"traductor: {type(e).__name__}: {e}")
        return
    _check(problems, sql, params)


class _Cursor(FakeCursor):
    def __init__(self, cursor, problems: list[str], flt, proj) -> None:
        super().__init__(cursor)
        self._problems, self._flt, self._proj = problems, flt, proj
        self._sort: list = []
        self._skip_n = self._limit_n = None

    def sort(self, key_or_list, direction=None):
        self._sort = ([(key_or_list, 1 if direction is None else direction)] if isinstance(key_or_list, str)
                      else list(key_or_list))
        return super().sort(key_or_list, direction)

    def skip(self, n):
        self._skip_n = n
        return super().skip(n)

    def limit(self, n):
        self._limit_n = n
        return super().limit(n)

    def _build(self):
        s = Sql()
        proj = projection_expr(self._proj, s)
        where = filter_sql(self._flt, s, doc="doc", mode="table")
        order = order_by_find(self._sort, s) if self._sort else ""
        sql = f"SELECT {proj} AS doc FROM t WHERE {where} {order}"
        if self._limit_n is not None:
            sql += f" LIMIT {s.add(self._limit_n)}"
        if self._skip_n is not None:
            sql += f" OFFSET {s.add(self._skip_n)}"
        return sql, s.params

    async def to_list(self, length=None):
        _translate(self._problems, self._build)
        return await super().to_list(length)

    def __aiter__(self):
        _translate(self._problems, self._build)
        return super().__aiter__()


class _AggCursor(FakeCursor):
    def __init__(self, cursor, problems: list[str], pipeline) -> None:
        super().__init__(cursor)
        self._problems, self._pipeline = problems, pipeline

    def _build(self):
        s = Sql()
        return compile_pipeline(self._pipeline, "t", s), s.params

    async def to_list(self, length=None):
        _translate(self._problems, self._build)
        return await super().to_list(length)

    def __aiter__(self):
        _translate(self._problems, self._build)
        return super().__aiter__()


class _Collection(FakeCollection):
    def __init__(self, coll, problems: list[str]) -> None:
        super().__init__(coll)
        self._problems = problems

    def find(self, filter=None, projection=None):
        return _Cursor(self._c.find(filter or {}, projection), self._problems, filter, projection)

    async def find_one(self, filter=None, projection=None):
        docs = await self.find(filter, projection).limit(1).to_list(1)
        return docs[0] if docs else None

    async def count_documents(self, filter=None, maxTimeMS=None):
        def build():
            s = Sql()
            return f"SELECT count(*) FROM t WHERE {filter_sql(filter, s)}", s.params
        _translate(self._problems, build)
        return self._c.count_documents(filter or {})

    def aggregate(self, pipeline, maxTimeMS=None, **_: Any):
        # Lakebase (como Mongo): un `$group` con `_id: None` sobre CERO filas no
        # devuelve ningún grupo (`HAVING count(*) > 0`); mongomock inventa uno
        # (`[{'n': 0}]`) y escondería esa diferencia.
        for i, stage in enumerate(pipeline):
            if "$group" in stage and stage["$group"].get("_id") is None \
                    and not list(self._c.aggregate(pipeline[:i])):
                return _AggCursor(iter([]), self._problems, pipeline)
        return _AggCursor(self._c.aggregate(pipeline), self._problems, pipeline)


class LakebaseCheckingDb(FakeDb):
    """`FakeDb` + `problems`: lo que el SQL de Lakebase no soportaría."""

    def __init__(self) -> None:
        super().__init__()
        self.problems: list[str] = []

    def __getitem__(self, name: str) -> FakeCollection:
        return _Collection(self.raw[name], self.problems)
