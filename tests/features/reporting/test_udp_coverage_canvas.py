"""F5 — udp_coverage con level='canvas': los totales/pairs salen de
`subject_areas`, no de canonical_tables (fallback previo, que daba cobertura
falsa). Fakes de db/_coverage_pairs — sin Mongo."""
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


def test_udp_coverage_def_sin_level_no_500(monkeypatch):
    """Adjudicación #4 (final review): udp_coverage lee docs Mongo CRUDOS (sin
    validar contra UdpDefinitionDoc) — una def escrita por script/consola sin
    `level` hacía crashear el sort (None < str) y tiraba 500 todo Insights.
    `(level or "")` en la key: la def rara ordena primero y usa el fallback
    de tablas, sin romper el endpoint."""
    defs = [{"_id": "u1", "name": "Sin nivel", "dataType": "string"},   # level ausente
            {"_id": "u2", "name": "Área", "level": "column", "dataType": "string"}]
    db = _FakeDb({"udp_definitions": _FakeColl(docs=defs),
                  "canonical_columns": _FakeColl(count=100),
                  "canonical_tables": _FakeColl(count=10),
                  "subject_areas": _FakeColl(count=5),
                  "views": _FakeColl(count=0)})
    monkeypatch.setattr(views, "get_db", AsyncMock(return_value=db))

    async def _fake_pairs(coll):
        return {}

    monkeypatch.setattr(views, "_coverage_pairs", _fake_pairs)

    rows = asyncio.run(views.udp_coverage())
    assert [r["defId"] for r in rows] == ["u1", "u2"]   # None-level primero ("")
    assert rows[0]["level"] is None
    assert rows[0]["totalEntities"] == 10               # fallback: canonical_tables


def test_udp_coverage_nivel_canvas(monkeypatch):
    defs = [{"_id": "u9", "name": "Dominio funcional", "level": "canvas",
             "dataType": "list", "allowedValues": ["Riesgos", "Finanzas"]}]
    db = _FakeDb({"udp_definitions": _FakeColl(docs=defs),
                  "canonical_columns": _FakeColl(count=100),
                  "canonical_tables": _FakeColl(count=10),
                  "subject_areas": _FakeColl(count=5),
                  "views": _FakeColl(count=0)})
    monkeypatch.setattr(views, "get_db", AsyncMock(return_value=db))

    async def _fake_pairs(coll):
        return {"u9": {"Riesgos": 2, "Finanzas": 1}} if coll == "subject_areas" else {}

    monkeypatch.setattr(views, "_coverage_pairs", _fake_pairs)

    rows = asyncio.run(views.udp_coverage())
    row = rows[0]
    assert row["level"] == "canvas"
    assert row["totalEntities"] == 5      # subject_areas, NO canonical_tables (10)
    assert row["setCount"] == 3
    assert row["missingCount"] == 2
    assert row["coveragePct"] == 0.6
