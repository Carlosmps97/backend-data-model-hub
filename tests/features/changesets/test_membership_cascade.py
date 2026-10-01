"""Doc 88 §4 — borrar una vista (o tabla) en el draft no deja su id colgando en
los canvases: poda de miembros borrados en el gate + cascada server-side al
delete. Antes: «View X doesn't exist in this project» al mover una tabla o al
publicar, porque el canvas seguía listando la vista borrada."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.features.changesets import service
from app.features.changesets.validation import CrossProjectError

SA_PUB = {"id": "sa1", "projectId": "p1", "name": "D", "tableIds": ["t1"], "viewIds": ["v1", "v2"],
          "layout": {"t1": {"x": 1, "y": 1}, "v1": {"x": 2, "y": 2}, "v2": {"x": 3, "y": 3}}, "drawings": [], "udpValues": {}}


def _cs(monkeypatch, pending: dict | None = None):
    monkeypatch.setattr(service.repository, "get",
                        AsyncMock(return_value={"projectId": "p1", "id": "cs1", "owner": "ana", "status": "draft"}))
    monkeypatch.setattr(service.repository, "changes_map",
                        AsyncMock(side_effect=lambda cs_id, colls=None: {c: dict((pending or {}).get(c, {})) for c in (colls or pending or {})}))
    monkeypatch.setattr(service.settings_service, "get_naming_for", AsyncMock(return_value={"maxLength": 150}))
    monkeypatch.setattr(service.dict_svc, "naming_rules", AsyncMock(return_value=({}, "", "upper")))


def test_prune_deleted_members_quita_ids_borrados_en_el_draft_y_su_layout():
    pending = {"views": {"v1": {"op": "delete"}}, "canonical_tables": {"t9": {"op": "delete"}}}
    out = service.prune_deleted_members({**SA_PUB, "tableIds": ["t1", "t9"]}, pending)
    assert out["tableIds"] == ["t1"] and out["viewIds"] == ["v2"]
    assert set(out["layout"]) == {"t1", "v2"}
    assert service.prune_deleted_members({"viewIds": None, "tableIds": ["t1"]}, pending)["viewIds"] is None   # legacy intacto
    same = {"tableIds": ["t1"], "viewIds": ["v2"]}
    assert service.prune_deleted_members(same, pending) is same                                          # nada que podar


def test_upsert_de_canvas_con_vista_borrada_en_el_draft_se_graba_podado(monkeypatch):
    _cs(monkeypatch, pending={"views": {"v1": {"op": "delete"}}, "canonical_tables": {}, "folders": {}})

    async def _pub(collection, flt=None, **kw):
        return [{"id": "t1", "projectId": "p1"}] if collection == "canonical_tables" else \
               ([{"id": "v2", "projectId": "p1"}] if collection == "views" else [])
    monkeypatch.setattr(service.repository, "published", _pub)
    set_change = AsyncMock(return_value={"id": "cs1"})
    monkeypatch.setattr(service.repository, "set_change", set_change)
    asyncio.run(service.add_change("cs1", "ana", "subject_areas", "sa1", "upsert", dict(SA_PUB)))
    payload = set_change.await_args.args[4]
    assert payload["viewIds"] == ["v2"] and "v1" not in payload["layout"]


def test_un_id_desconocido_sigue_siendo_error(monkeypatch):
    _cs(monkeypatch, pending={"views": {}, "canonical_tables": {}, "folders": {}})
    monkeypatch.setattr(service.repository, "published", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "set_change", AsyncMock())
    with pytest.raises(CrossProjectError):
        asyncio.run(service.add_change("cs1", "ana", "subject_areas", "sa1", "upsert", {**SA_PUB, "tableIds": [], "viewIds": ["zz"]}))


def test_borrar_una_vista_cascadea_a_los_canvases_publicados_y_pendientes(monkeypatch):
    pending_sa = {"sa2": {"op": "upsert", "payload": {**SA_PUB, "id": "sa2", "name": "D2", "viewIds": ["v1"],
                                                       "layout": {"t1": {"x": 0, "y": 0}, "v1": {"x": 9, "y": 9}}}},
                  "sa3": {"op": "upsert", "payload": {**SA_PUB, "id": "sa3", "viewIds": ["v2"]}}}
    _cs(monkeypatch, pending={"subject_areas": pending_sa, "views": {}, "canonical_tables": {}, "folders": {}})
    calls = []

    async def _pub(collection, flt=None, **kw):
        calls.append((collection, flt))
        if collection == "subject_areas":
            return [dict(SA_PUB), {**SA_PUB, "id": "sa3", "viewIds": ["v1"]}]   # sa3 publicado la lista, pero el pendiente ya no
        return [{"id": "t1", "projectId": "p1"}] if collection == "canonical_tables" else \
               ([{"id": "v2", "projectId": "p1"}] if collection == "views" else [])
    monkeypatch.setattr(service.repository, "published", _pub)
    bulk = AsyncMock(return_value={"id": "cs1"})
    monkeypatch.setattr(service.repository, "set_changes_bulk", bulk)

    asyncio.run(service.add_change("cs1", "ana", "views", "v1", "delete", None))

    assert ("subject_areas", {"projectId": "p1", "viewIds": "v1"}) in calls
    # Doc 104 (ronda 2): canvases podados y el borrado van en UNA escritura
    # condicionada al dueño (todo o nada); el borrado, al final.
    bulk.assert_awaited_once()
    assert bulk.await_args.kwargs["owner"] == "ana"
    items = bulk.await_args.args[1]
    written = [(it["collection"], it["entityId"], it["op"]) for it in items]
    assert written[-1] == ("views", "v1", "delete")
    canvases = {it["entityId"]: it["payload"] for it in items if it["collection"] == "subject_areas"}
    assert set(canvases) == {"sa1", "sa2"}                     # sa3: el pendiente ya no la lista → nada que sanear
    assert canvases["sa1"]["viewIds"] == ["v2"] and set(canvases["sa1"]["layout"]) == {"t1", "v2"}
    assert canvases["sa2"]["viewIds"] == [] and set(canvases["sa2"]["layout"]) == {"t1"}
    assert canvases["sa1"]["projectId"] == "p1"


def test_lote_con_borrado_de_vista_poda_el_canvas_del_lote_y_cascadea_al_publicado(monkeypatch):
    _cs(monkeypatch, pending={"subject_areas": {}, "views": {}, "canonical_tables": {}, "folders": {}})

    async def _pub(collection, flt=None, **kw):
        if collection == "subject_areas":
            return [dict(SA_PUB), {**SA_PUB, "id": "sa9", "viewIds": ["v1"]}]
        return [{"id": "t1", "projectId": "p1"}] if collection == "canonical_tables" else \
               ([{"id": "v2", "projectId": "p1"}] if collection == "views" else [])
    monkeypatch.setattr(service.repository, "published", _pub)
    bulk = AsyncMock(return_value={"id": "cs1"})
    monkeypatch.setattr(service.repository, "set_changes_bulk", bulk)

    asyncio.run(service.add_changes_bulk("cs1", "ana", [
        {"collection": "views", "entityId": "v1", "op": "delete", "payload": None},
        {"collection": "subject_areas", "entityId": "sa1", "op": "upsert", "payload": dict(SA_PUB)},   # aún lista v1
    ]))
    items = {(it["collection"], it["entityId"]): it for it in bulk.await_args.args[1]}
    assert set(items) == {("views", "v1"), ("subject_areas", "sa1"), ("subject_areas", "sa9")}
    assert items[("subject_areas", "sa1")]["payload"]["viewIds"] == ["v2"]          # podado en el gate
    assert items[("subject_areas", "sa9")]["payload"]["viewIds"] == []              # cascada al publicado fuera del lote
