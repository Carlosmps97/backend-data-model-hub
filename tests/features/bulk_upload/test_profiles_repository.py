"""Doc 78 §3.1: CRUD scoped de `upload_profiles`; un default por proyecto."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.core.scope import PROJECT_SCOPED
from app.features.bulk_upload.profiles import repository

BODY = {"name": "P", "sheets": {"tables": {"name": "T", "mappings": []}, "columns": {"name": "C", "mappings": []}}}


def _db(coll):
    return AsyncMock(return_value={"upload_profiles": coll})


def test_coleccion_es_de_alcance_de_proyecto():
    assert "upload_profiles" in PROJECT_SCOPED


def test_list_filtra_por_proyecto_y_ordena_default_primero(monkeypatch):
    cursor = MagicMock(); cursor.to_list = AsyncMock(return_value=[
        {"_id": "b", "projectId": "p1", "name": "Zeta", "isDefault": False, "sheets": {}, "flgactive": True},
        {"_id": "a", "projectId": "p1", "name": "alfa", "isDefault": True, "sheets": {}, "flgactive": True, "createdAt": "t0"}])
    coll = MagicMock(); coll.find = MagicMock(return_value=cursor)
    monkeypatch.setattr(repository, "get_db", _db(coll))
    out = asyncio.run(repository.list_profiles("p1"))
    assert [p["id"] for p in out] == ["a", "b"] and out[0]["createdAt"] == "t0" and "flgactive" not in out[0]
    assert coll.find.call_args.args[0] == {"projectId": "p1", "flgactive": {"$ne": False}}


def test_create_estampa_proyecto_y_auditoria(monkeypatch):
    coll = MagicMock(); coll.insert_one = AsyncMock()
    monkeypatch.setattr(repository, "get_db", _db(coll))
    out = asyncio.run(repository.create_profile("p1", {**BODY, "createdBy": "ana"}))
    doc = coll.insert_one.call_args.args[0]
    assert (out["projectId"], doc["projectId"], doc["flgactive"], doc["createdBy"]) == ("p1", "p1", True, "ana")
    assert doc["_id"] == out["id"] and "id" not in doc and out["createdAt"]


def test_update_y_delete_acotados_al_proyecto(monkeypatch):
    coll = MagicMock()
    coll.find_one_and_update = AsyncMock(return_value={"_id": "a", "projectId": "p1", **BODY, "flgactive": True})
    coll.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    monkeypatch.setattr(repository, "get_db", _db(coll))
    out = asyncio.run(repository.update_profile("p1", "a", {**BODY, "updatedBy": "ana"}))
    assert out["id"] == "a"
    assert coll.find_one_and_update.call_args.args[0] == {"projectId": "p1", "_id": "a", "flgactive": {"$ne": False}}
    assert "createdBy" not in coll.find_one_and_update.call_args.args[1]["$set"]
    assert asyncio.run(repository.delete_profile("p1", "a")) is True
    assert coll.update_one.call_args.args[0]["projectId"] == "p1"
    assert coll.update_one.call_args.args[1]["$set"]["isDefault"] is False


def test_get_none_si_no_existe(monkeypatch):
    coll = MagicMock(); coll.find_one = AsyncMock(return_value=None)
    monkeypatch.setattr(repository, "get_db", _db(coll))
    assert asyncio.run(repository.get_profile("p1", "zz")) is None
    assert coll.find_one.call_args.args[0]["projectId"] == "p1"


def test_set_default_desmarca_los_demas(monkeypatch):
    coll = MagicMock()
    coll.update_many = AsyncMock()
    coll.find_one_and_update = AsyncMock(return_value={"_id": "a", "projectId": "p1", **BODY, "isDefault": True, "flgactive": True})
    monkeypatch.setattr(repository, "get_db", _db(coll))
    out = asyncio.run(repository.set_default("p1", "a"))
    assert out["isDefault"] is True
    assert coll.update_many.call_args.args[0] == {"projectId": "p1", "_id": {"$ne": "a"}, "isDefault": True}
