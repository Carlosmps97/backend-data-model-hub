"""Doc 75 I5: restore_udp sólo toca las defs DEL proyecto; list/create por proyecto."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.features.udp import repository


def test_restore_udp_soft_delete_acotado_al_proyecto(monkeypatch):
    coll = MagicMock(); coll.update_many = AsyncMock(); coll.bulk_write = AsyncMock()
    monkeypatch.setattr(repository, "get_db", AsyncMock(return_value={"udp_definitions": coll}))
    asyncio.run(repository.restore_udp("p1", [{"id": "u1", "name": "X", "level": "table", "view": "physical"}]))
    flt = coll.update_many.call_args.args[0]
    assert flt["projectId"] == "p1" and flt["_id"] == {"$nin": ["u1"]}
    ops = coll.bulk_write.call_args.args[0]
    assert ops[0]._doc["$set"]["projectId"] == "p1"


def test_list_udp_filtra_por_proyecto(monkeypatch):
    cursor = MagicMock(); cursor.to_list = AsyncMock(return_value=[])
    coll = MagicMock(); coll.find = MagicMock(return_value=cursor)
    monkeypatch.setattr(repository, "get_db", AsyncMock(return_value={"udp_definitions": coll}))
    assert asyncio.run(repository.list_udp("p1")) == []
    assert coll.find.call_args.args[0]["projectId"] == "p1"


def test_create_udp_estampa_project_id(monkeypatch):
    coll = MagicMock(); coll.insert_one = AsyncMock()
    monkeypatch.setattr(repository, "get_db", AsyncMock(return_value={"udp_definitions": coll}))
    out = asyncio.run(repository.create_udp("p1", {"name": "Particion", "level": "column"}))
    assert out["projectId"] == "p1" and coll.insert_one.call_args.args[0]["projectId"] == "p1"
