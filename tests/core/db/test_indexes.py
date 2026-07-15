"""`ensure_indexes` declara el índice F3a `views.sourceTableIds` (lo usan la
query del diagrama y `list_all`). DB fake que registra las llamadas — no toca
Mongo ni Cosmos."""
from __future__ import annotations

import asyncio

from app.core.db.indexes import ensure_indexes


class _FakeColl:
    def __init__(self, name: str, calls: list):
        self._name, self._calls = name, calls

    async def create_index(self, keys, **kwargs):
        self._calls.append((self._name, tuple(keys)))


class _FakeDb:
    def __init__(self):
        self.calls: list = []

    def __getitem__(self, name: str) -> _FakeColl:
        return _FakeColl(name, self.calls)


def test_views_source_table_ids_index_declared():
    db = _FakeDb()
    asyncio.run(ensure_indexes(db))
    assert ("views", (("sourceTableIds", 1),)) in db.calls
    # El índice legacy por tableId sigue (fallback OR de docs no migrados).
    assert ("views", (("tableId", 1),)) in db.calls
    assert ("views", (("flgactive", 1),)) in db.calls
