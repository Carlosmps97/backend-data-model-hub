"""Doc 75 I5 — el restore de estándares NUNCA toca otro proyecto: cada restore_*
filtra por projectId en el soft-delete y estampa projectId en los upserts."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.features.data_standards import repository


def _coll():
    c = MagicMock(); c.update_many = AsyncMock(); c.bulk_write = AsyncMock(); c.update_one = AsyncMock()
    return c


@pytest.mark.parametrize("fn,coll,snap", [
    (repository.restore_domains, "parent_domains", [{"id": "d1", "name": "Codigo", "defaultDataType": "VARCHAR(20)"}]),
    (repository.restore_dict, "glossary_terms", [{"id": "t1", "term": "codigo", "abbrev": "COD", "scope": "column"}]),
])
def test_restore_soft_delete_solo_del_proyecto(monkeypatch, fn, coll, snap):
    c = _coll()
    monkeypatch.setattr(repository, "get_db", AsyncMock(return_value={coll: c}))
    asyncio.run(fn("p1", snap))
    flt = c.update_many.call_args.args[0]
    assert flt["projectId"] == "p1", "sin projectId el restore borraría los estándares de los demás proyectos"
    assert flt["_id"] == {"$nin": [snap[0]["id"]]}
    op = c.bulk_write.call_args.args[0][0]
    assert op._doc["$set"]["projectId"] == "p1"


def test_restore_naming_usa_id_por_proyecto(monkeypatch):
    c = _coll()
    monkeypatch.setattr(repository, "get_db", AsyncMock(return_value={"naming_config": c}))
    asyncio.run(repository.restore_naming("p1", {"column": {"separator": "_", "case": "upper", "maxLength": 150}}))
    assert c.update_one.call_args.args[0] == {"_id": "p1:column"}
    assert c.update_one.call_args.args[1]["$set"]["projectId"] == "p1"


def test_next_seq_es_por_proyecto(monkeypatch):
    c = MagicMock()
    cursor = MagicMock(); cursor.to_list = AsyncMock(return_value=[{"seq": 4, "projectId": "p1"}])
    c.find = MagicMock(return_value=cursor); c.insert_one = AsyncMock()
    monkeypatch.setattr(repository, "get_db", AsyncMock(return_value={"standards_versions": c}))
    v = asyncio.run(repository.insert_version_next_seq("p1", {"kind": "batch", "title": "t", "author": "a"}))
    assert c.find.call_args.args[0]["projectId"] == "p1"
    assert v["seq"] == 5 and v["label"] == "v5" and v["projectId"] == "p1"
