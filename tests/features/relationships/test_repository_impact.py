"""`list_for_column` / `canvases_containing` (spec 10 §8) — db fake, sin Cosmos."""
from __future__ import annotations

import asyncio

from app.features.relationships import repository


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, n):
        return self._docs


class _FakeColl:
    def __init__(self, docs, seen):
        self._docs = docs
        self._seen = seen

    def find(self, flt, projection=None):
        self._seen.append(flt)
        return _FakeCursor(self._docs)


class _FakeDb:
    def __init__(self, by_coll, seen):
        self._by = by_coll
        self._seen = seen

    def __getitem__(self, name):
        return _FakeColl(self._by.get(name, []), self._seen)


def _patch_db(monkeypatch, by_coll):
    seen: list[dict] = []

    async def _get_db():
        return _FakeDb(by_coll, seen)

    monkeypatch.setattr(repository, "get_db", _get_db)
    return seen


REL = {"_id": "r1", "sourceTableId": "tA", "sourceColumnId": "cA", "targetTableId": "tB",
       "targetColumnId": "cB", "sourceCardinality": "one", "targetCardinality": "many",
       "identifying": False, "flgactive": True}


def test_list_for_column_filtra_por_ambos_extremos(monkeypatch):
    seen = _patch_db(monkeypatch, {"relationships": [REL]})
    out = asyncio.run(repository.list_for_column("cA"))
    assert [r["id"] for r in out] == ["r1"]
    assert seen[0]["$or"] == [{"sourceColumnId": "cA"}, {"targetColumnId": "cA"}]
    assert seen[0]["flgactive"] == {"$ne": False}


def test_list_for_column_vacio_sin_id(monkeypatch):
    _patch_db(monkeypatch, {})
    assert asyncio.run(repository.list_for_column("")) == []


def test_canvases_containing_proyecta_id_name_tableids(monkeypatch):
    seen = _patch_db(monkeypatch, {"subject_areas": [
        {"_id": "sa1", "name": "Ventas", "tableIds": ["tA", "tB"]}]})
    out = asyncio.run(repository.canvases_containing("tA"))
    assert out == [{"id": "sa1", "name": "Ventas", "tableIds": ["tA", "tB"]}]
    assert seen[0]["tableIds"] == "tA"


def test_canvases_containing_vacio_sin_id(monkeypatch):
    _patch_db(monkeypatch, {})
    assert asyncio.run(repository.canvases_containing("")) == []
