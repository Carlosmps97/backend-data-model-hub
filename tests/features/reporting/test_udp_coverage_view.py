"""Doc 61 — udp_coverage con level='view': totales/pairs salen de `views`
(sin el nivel, un def de vistas caería al fallback de tablas con cobertura
falsa). Fakes de db/_coverage_pairs — sin Mongo (patrón canvas)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.reporting import views


class _FakeColl:
    def __init__(self, docs=None, count=0):
        self._docs, self._count = docs or [], count

    def find(self, *_a, **_k):
        return self

    async def to_list(self, _n):
        return self._docs

    async def count_documents(self, *_a, **_k):
        return self._count


class _FakeDb:
    def __init__(self, colls: dict):
        self._colls = colls

    def __getitem__(self, name: str) -> _FakeColl:
        return self._colls[name]


def test_udp_coverage_nivel_view(monkeypatch):
    defs = [{"_id": "u-vt", "name": "View Type", "level": "view",
             "dataType": "list", "allowedValues": ["Regular", "Personalizada"]}]
    db = _FakeDb({"udp_definitions": _FakeColl(docs=defs),
                  "canonical_columns": _FakeColl(count=100),
                  "canonical_tables": _FakeColl(count=10),
                  "subject_areas": _FakeColl(count=5),
                  "views": _FakeColl(count=7)})
    monkeypatch.setattr(views, "get_db", AsyncMock(return_value=db))

    async def _fake_pairs(project_id, coll):
        return {"u-vt": {"Personalizada": 2}} if coll == "views" else {}

    monkeypatch.setattr(views, "_coverage_pairs", _fake_pairs)

    rows = asyncio.run(views.udp_coverage("p1"))
    row = rows[0]
    assert row["level"] == "view"
    assert row["totalEntities"] == 7      # views, NO canonical_tables (10)
    assert row["setCount"] == 2
    assert row["missingCount"] == 5
    assert row["invalidCount"] == 0
