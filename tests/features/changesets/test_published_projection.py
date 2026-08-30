"""`repository.published(projection=)` (doc 55): la carga masiva lee TODO el
pool de tablas efectivo para resolver identidades por nombre — proyectar los
campos de nombre evita mover docs completos (udpValues, descripciones)."""
from __future__ import annotations

import asyncio

from app.features.changesets import repository


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *a):
        return self

    def limit(self, n):
        return self

    async def to_list(self, n):
        return self._docs


class _Coll:
    def __init__(self, state):
        self.s = state

    def find(self, flt, projection=None):
        self.s["calls"].append((flt, projection))
        return _Cursor([{"_id": "t1", "physicalName": "A", "flgactive": True}])


class _Db:
    def __init__(self, state):
        self.s = state

    def __getitem__(self, name):
        return _Coll(self.s)


def test_published_pasa_la_proyeccion_al_find(monkeypatch):
    state = {"calls": []}

    async def _get_db():
        return _Db(state)

    monkeypatch.setattr(repository, "get_db", _get_db)
    out = asyncio.run(repository.published("canonical_tables", projection={"physicalName": 1}))
    assert state["calls"] == [({"flgactive": {"$ne": False}}, {"physicalName": 1})]
    assert out == [{"id": "t1", "physicalName": "A"}]


def test_published_sin_proyeccion_conserva_el_contrato(monkeypatch):
    state = {"calls": []}

    async def _get_db():
        return _Db(state)

    monkeypatch.setattr(repository, "get_db", _get_db)
    asyncio.run(repository.published("canonical_tables", {"tableId": "x"}))
    assert state["calls"] == [({"flgactive": {"$ne": False}, "tableId": "x"}, None)]
