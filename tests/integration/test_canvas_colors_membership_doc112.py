"""Doc 112 — el color de una caja en un canvas sale con la caja, de punta a
punta (router → service → repos reales sobre la BD en memoria): borrar la vista
en un draft le quita su color en el canvas (draft y, al publicar, producción);
los colores de las demás cajas no se tocan, y el rollback a la versión anterior
lo repone con la caja."""
from __future__ import annotations

from tests.integration.conftest import publish


def _draft(api, w, user="ana"):
    return api(user).post("/api/changesets/snapshot", {"projectId": w["pid"]}, expect=201)["id"]


def _canvas(w, **over):
    return {"projectId": w["pid"], "folderId": w["folder"], "name": "Modelo Clientes",
            "tableIds": [w["t1"], w["t2"]], "viewIds": [w["view"]],
            "layout": {w["t1"]: {"x": 0, "y": 0}, w["t2"]: {"x": 400, "y": 0}, w["view"]: {"x": 0, "y": 300}}, **over}


def test_borrar_la_vista_le_quita_su_color_del_canvas_y_el_rollback_lo_repone(api, world):
    ana, w = api("ana"), world
    cs = _draft(api, w)
    ana.change(cs, "subject_areas", w["canvas"], _canvas(w, colors={w["view"]: "#92d050", w["t2"]: "none"}))
    painted = publish(api, cs)

    cs2 = _draft(api, w)
    ana.change(cs2, "views", w["view"], None, op="delete")
    dia = ana.get(f"/api/subject-areas/{w['canvas']}/diagram?changesetId={cs2}")
    assert dia["subjectArea"]["colors"] == {w["t2"]: "none"}
    assert w["view"] not in dia["subjectArea"]["layout"]
    publish(api, cs2)
    prod = ana.get(f"/api/subject-areas/{w['canvas']}/diagram")
    assert prod["subjectArea"]["colors"] == {w["t2"]: "none"}

    # volver a la versión que la tenía pintada: vuelve la vista con su color
    rb = ana.post(f"/api/changesets/{painted['id']}/rollback", {})
    publish(api, rb["id"])
    prod = ana.get(f"/api/subject-areas/{w['canvas']}/diagram")
    assert prod["subjectArea"]["colors"] == {w["view"]: "#92D050", w["t2"]: "none"}
    assert w["view"] in prod["subjectArea"]["viewIds"]
