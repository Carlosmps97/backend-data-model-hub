"""Doc 100 — P1/P2: llaves de primer nivel que el `$set` del publish lee como
OTRA cosa. El publish copia cada llave del cambio tal cual a la base y la
validación contra el modelo ignora las que no conoce: `layout.evil` escribía
DENTRO del layout (el canvas y la lista de canvases dejaban de leerse), `$x`
hacía fallar el approve y `_id` cambiaba la identidad del registro.

Arreglo: la entrada las rechaza (422), la lectura del draft no las devuelve y
el publish no las escribe. Router → service → repositorio REALES; sólo el
driver de BD es falso."""
from __future__ import annotations

import pytest

from app.features.changesets.repository import change_key
from tests.integration.conftest import publish

RESERVED = ("layout.evil", "$foo", "_id")


def _canvas(w: dict, **over) -> dict:
    return {"projectId": w["pid"], "folderId": w["folder"], "name": "Modelo Clientes",
            "tableIds": [w["t1"], w["t2"]], "viewIds": [w["view"]],
            "layout": {w["t1"]: {"x": 0, "y": 0}, w["t2"]: {"x": 400, "y": 0}}, **over}


def _draft(api, w: dict, user: str = "ana") -> str:
    return api(user).post("/api/changesets/snapshot", {"projectId": w["pid"]}, expect=201)["id"]


@pytest.mark.parametrize("key", RESERVED)
def test_la_entrada_rechaza_la_llave_en_un_cambio_y_en_el_lote(api, world, key):
    ana = api("ana")
    w = world
    cs = _draft(api, w)
    payload = {**_canvas(w), key: {"x": "a"}}
    status, body = ana.change(cs, "subject_areas", w["canvas"], payload, expect=422)
    assert key in str(body)
    status, body = ana.bulk(cs, [{"collection": "subject_areas", "entityId": w["canvas"], "op": "upsert",
                                  "payload": payload}], expect=422)
    assert key in str(body)
    # nada quedó grabado en el draft
    assert ana.get(f"/api/changesets/{cs}/effective/subject_areas?ids={w['canvas']}")[0]["layout"] == _canvas(w)["layout"]


def test_la_regla_vale_para_las_tablas(api, world):
    ana = api("ana")
    w = world
    cs = _draft(api, w)
    t1 = ana.get(f"/api/changesets/{cs}/effective/canonical_tables?ids={w['t1']}")[0]
    status, body = ana.change(cs, "canonical_tables", w["t1"],
                              {**{k: v for k, v in t1.items() if k != "id"}, "udpValues.x": "v"}, expect=422)
    assert "udpValues.x" in str(body)


def test_un_cambio_viejo_con_llaves_reservadas_publica_sin_romper_nada(api, world, fake_db):
    """Cambio grabado ANTES del arreglo (se simula escribiendo el documento del
    cambio a mano): la lectura del draft no devuelve esas llaves — el front las
    mandaría de vuelta y la entrada lo bloquearía — y el publish no las escribe."""
    ana = api("ana")
    w = world
    cs = _draft(api, w)
    moved = {w["t1"]: {"x": 10, "y": 20}, w["t2"]: {"x": 400, "y": 0}}
    ana.change(cs, "subject_areas", w["canvas"], _canvas(w, layout=moved))
    key = change_key(cs, "subject_areas", w["canvas"])
    doc = fake_db.raw["changeset_changes"].find_one({"_id": key})
    doc["payload"].update({"layout.evil": {"x": "a"}, "$foo": 1, "_id": "otro-id"})
    fake_db.raw["changeset_changes"].replace_one({"_id": key}, doc)

    eff = ana.get(f"/api/changesets/{cs}/effective/subject_areas?ids={w['canvas']}")[0]
    assert not {"layout.evil", "$foo", "_id"} & set(eff)
    dia = ana.get(f"/api/subject-areas/{w['canvas']}/diagram?changesetId={cs}")
    assert not {"layout.evil", "$foo", "_id"} & set(dia["subjectArea"])

    publish(api, cs)   # antes: el approve fallaba ($foo) o corrompía el canvas (layout.evil)
    stored = fake_db.raw["subject_areas"].find_one({"_id": w["canvas"]})
    assert stored["_id"] == w["canvas"] and "$foo" not in stored
    assert stored["layout"] == moved
    # el canvas y la lista de canvases del proyecto se leen
    assert ana.get(f"/api/subject-areas/{w['canvas']}")["layout"] == moved
    assert w["canvas"] in {sa["id"] for sa in ana.get(f"/api/projects/{w['pid']}/subject-areas")}


def test_renombrar_un_esquema_no_choca_con_un_cambio_viejo_con_llave_reservada(api, world, fake_db):
    """Revisión independiente 2026-09-28: el rename de esquema arma, en el
    SERVIDOR, los payloads de sus tablas y vistas y los graba con `add_change`.
    Si los armaba sin pasar por la lectura que limpia (overlay), un cambio viejo
    con una llave reservada daba 422 y dejaba el draft a medio renombrar."""
    ana = api("ana")
    w = world
    cs = _draft(api, w)
    t1 = ana.get(f"/api/changesets/{cs}/effective/canonical_tables?ids={w['t1']}")[0]
    ana.change(cs, "canonical_tables", w["t1"], {**{k: v for k, v in t1.items() if k != "id"}, "description": "editada"})
    key = change_key(cs, "canonical_tables", w["t1"])
    doc = fake_db.raw["changeset_changes"].find_one({"_id": key})
    doc["payload"]["udpValues.x"] = "v"
    fake_db.raw["changeset_changes"].replace_one({"_id": key}, doc)

    ana.post(f"/api/changesets/{cs}/schemas/{w['schema']}/rename", {"newName": "STG2"})
    tabla = ana.get(f"/api/changesets/{cs}/effective/canonical_tables?ids={w['t1']}")[0]
    assert tabla["schema"] == "STG2" and tabla["description"] == "editada"
    assert "udpValues.x" not in fake_db.raw["changeset_changes"].find_one({"_id": key})["payload"]
