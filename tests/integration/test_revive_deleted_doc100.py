"""Doc 100 (P3) — un draft que tocó una entidad que OTRO draft borró y publicó
antes. Regla del owner: si ambos tocaron lo mismo, gana el draft que publica
(la entidad vuelve), PERO la revisión lo avisa como conflicto y el documento
reactivado queda sin su marca de borrado. Un rollback que restaura algo
borrado ANTES de abrirse es deliberado: sin aviso."""
from __future__ import annotations

from tests.integration.conftest import publish


def _canvas(w: dict, **over) -> dict:
    return {"projectId": w["pid"], "folderId": w["folder"], "name": "Modelo Clientes",
            "tableIds": [w["t1"], w["t2"]], "viewIds": [w["view"]],
            "layout": {w["t1"]: {"x": 0, "y": 0}, w["t2"]: {"x": 400, "y": 0}}, **over}


def _draft(api, w: dict, user: str = "ana") -> str:
    return api(user).post("/api/changesets/snapshot", {"projectId": w["pid"]}, expect=201)["id"]


def _item(diff: dict, collection: str, entity_id: str) -> tuple[str, dict]:
    for verb, items in diff["collections"][collection].items():
        for it in items:
            if it["id"] == entity_id:
                return verb, it
    raise AssertionError(f"{collection}/{entity_id} no está en el diff")


def test_un_canvas_que_otro_borro_vuelve_con_aviso_y_sin_marca_de_borrado(api, world, fake_db):
    ana, carla = api("ana"), api("carla")
    w = world
    cs_ana = _draft(api, w, "ana")
    moved = {w["t1"]: {"x": 10, "y": 20}, w["t2"]: {"x": 400, "y": 0}}
    ana.change(cs_ana, "subject_areas", w["canvas"], _canvas(w, layout=moved))   # sólo movió una tabla
    cs_carla = _draft(api, w, "carla")
    carla.change(cs_carla, "subject_areas", w["canvas"], None, op="delete")
    publish(api, cs_carla, owner="carla")
    assert ana.call("GET", f"/api/subject-areas/{w['canvas']}")[0] == 404

    verb, item = _item(ana.get(f"/api/changesets/{cs_ana}/diff"), "subject_areas", w["canvas"])
    assert verb == "added" and item["conflict"] is True and item["restores"] is True

    publish(api, cs_ana, owner="ana")                        # gana el draft (regla del owner)
    stored = fake_db.raw["subject_areas"].find_one({"_id": w["canvas"]})
    assert stored["flgactive"] is True
    assert "deletedAt" not in stored and "deletedIn" not in stored
    assert ana.get(f"/api/subject-areas/{w['canvas']}")["layout"] == moved


def test_un_rollback_que_restaura_lo_borrado_no_es_conflicto(api, world, fake_db):
    ana = api("ana")
    w = world
    cs = _draft(api, w)
    ana.change(cs, "subject_areas", w["canvas"], None, op="delete")
    publish(api, cs)
    assert fake_db.raw["subject_areas"].find_one({"_id": w["canvas"]})["flgactive"] is False

    restore = ana.post(f"/api/changesets/{w['v2']['id']}/rollback")
    verb, item = _item(ana.get(f"/api/changesets/{restore['id']}/diff"), "subject_areas", w["canvas"])
    assert verb == "added" and item["conflict"] is False and "restores" not in item

    publish(api, restore["id"], title="Restore v2")
    stored = fake_db.raw["subject_areas"].find_one({"_id": w["canvas"]})
    assert stored["flgactive"] is True and "deletedAt" not in stored
