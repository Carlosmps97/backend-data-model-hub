"""`repository.set_approval` con actores REALES (usernames SSO = correos).

Bug 2026-08-30: el guard `_safe_path_part` devolvía None para cualquier
username con punto (todo correo lo tiene) porque la decisión se escribía con
un dot-path `approvals.<actor>`; el router traducía ese None a un 409 falso
("The request is no longer in review…") y NINGÚN usuario SSO podía aprobar ni
rechazar un request. El fix escribe con `$mergeObjects`: la key viaja como
DATO jsonb, nunca como segmento de path.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.changesets import repository

ACTOR = "carlosmps97@hotmail.com"
ENTRY = {"status": "approved", "at": "2026-08-30T21:00:00+00:00"}


def _patch_db(monkeypatch, result):
    coll = AsyncMock()
    coll.find_one_and_update = AsyncMock(return_value=result)

    class _Db:
        def __getitem__(self, name):
            return coll

    async def _get_db():
        return _Db()

    monkeypatch.setattr(repository, "get_db", _get_db)
    return coll


def test_set_approval_acepta_username_con_puntos(monkeypatch):
    doc = {"_id": "c1", "title": "bb", "owner": "admin", "status": "submitted",
           "approvals": {ACTOR: ENTRY}}
    coll = _patch_db(monkeypatch, doc)

    res = asyncio.run(repository.set_approval("c1", ACTOR, ENTRY))

    assert res is not None
    assert res["approvals"] == {ACTOR: ENTRY}  # una sola key LITERAL (el correo entero)
    flt, update = coll.find_one_and_update.await_args.args
    assert flt == {"_id": "c1", "status": "submitted"}  # guard de estado intacto
    assert update["$mergeObjects"] == {"approvals": {ACTOR: ENTRY}}
    assert all("." not in k for k in update.get("$set", {}))  # sin paths dinámicos


def test_set_approval_none_si_dejo_de_estar_submitted(monkeypatch):
    _patch_db(monkeypatch, None)  # el filtro atómico no matcheó (withdraw ganó)
    assert asyncio.run(repository.set_approval("c1", ACTOR, ENTRY)) is None


def test_set_approval_actor_vacio_no_escribe(monkeypatch):
    coll = _patch_db(monkeypatch, {})
    assert asyncio.run(repository.set_approval("c1", "", ENTRY)) is None
    coll.find_one_and_update.assert_not_awaited()
