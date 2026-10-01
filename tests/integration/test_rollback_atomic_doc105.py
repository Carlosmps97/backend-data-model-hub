"""Doc 105 (A1-o2): el draft de restauración («Restore to vN») se arma con sus
cambios inversos en LOTES (antes un `set_change` por entidad: ~4 sentencias
por entidad y una caída en el medio dejaba un restore PARCIAL que se podía
enviar y aprobar). Hasta que quedan todos, la cabecera lleva
`restoreIncomplete` y no se puede enviar a revisión."""
from __future__ import annotations

import asyncio

import pytest

from app.features.changesets import repository, service
from tests.integration.conftest import publish, table_payload


def _two_versions(api, world):
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    ana.change(cs, "canonical_tables", world["t1"], table_payload("M_CLIENTE", "Cliente Nuevo"))
    ana.change(cs, "canonical_tables", "t-extra", table_payload("M_EXTRA", "Extra"))
    publish(api, cs)
    return cs


def test_restore_se_graba_en_un_lote_y_queda_completo(api, world, fake_db, monkeypatch):
    _two_versions(api, world)
    calls = {"one": 0, "bulk": 0}
    real_one, real_bulk = repository.set_change, repository.set_changes_bulk

    async def one(*a, **k):
        calls["one"] += 1
        return await real_one(*a, **k)

    async def bulk(*a, **k):
        calls["bulk"] += 1
        return await real_bulk(*a, **k)

    monkeypatch.setattr(repository, "set_change", one)
    monkeypatch.setattr(repository, "set_changes_bulk", bulk)
    draft = api("ana").post(f"/api/changesets/{world['v2']['id']}/rollback")
    assert calls == {"one": 0, "bulk": 1}
    assert not draft.get("restoreIncomplete")
    keys = {(d["collection"], d["entityId"]) for d in fake_db.raw["changeset_changes"].find({"csId": draft["id"]})}
    assert keys == {("canonical_tables", world["t1"]), ("canonical_tables", "t-extra")}


def test_restore_a_medias_no_se_puede_enviar(api, world, fake_db, monkeypatch):
    _two_versions(api, world)

    async def crash(*a, **k):
        raise TimeoutError("el proceso murió armando el restore")

    monkeypatch.setattr(repository, "set_changes_bulk", crash)
    with pytest.raises(TimeoutError):
        asyncio.run(service.rollback(world["v2"]["id"], "ana"))
    draft = next(d for d in fake_db.raw["changesets"].find({"title": {"$regex": "^Restore to"}}))
    assert draft["restoreIncomplete"] is True
    status, body = api("ana").call("POST", f"/api/changesets/{draft['_id']}/submit",
                                   {"title": "r", "reviewers": ["beto"]})
    assert status == 409 and "restore" in body["detail"].lower()
