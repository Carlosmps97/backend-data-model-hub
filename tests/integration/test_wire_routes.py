"""Trazos manuales de wires (doc 99) por HTTP, de punta a punta: el trazo se
graba en el draft con el documento del canvas, se ve en el canvas del draft, no
ensucia la revisión, se publica, y el rollback lo deshace. Router → service →
repositorio REALES; sólo el driver de BD es falso.

El trazo viaja POR CANVAS (`subject_areas.routes`), versionado igual que las
posiciones: la unidad de concurrencia es el documento del canvas."""
from __future__ import annotations

from tests.integration.conftest import publish

ROUTE = [{"x": 200, "y": 40}, {"x": 200, "y": 160}]
ROUTE_2 = [{"x": 260, "y": -30}]


def _canvas(w: dict, **over) -> dict:
    """Documento COMPLETO del canvas del mundo base (los payloads de un draft
    son siempre el doc completo)."""
    return {"projectId": w["pid"], "folderId": w["folder"], "name": "Modelo Clientes",
            "tableIds": [w["t1"], w["t2"]], "viewIds": [w["view"]],
            "layout": {w["t1"]: {"x": 0, "y": 0}, w["t2"]: {"x": 400, "y": 0}}, **over}


def _draft(api, w: dict, user: str = "ana") -> str:
    return api(user).post("/api/changesets/snapshot", {"projectId": w["pid"]}, expect=201)["id"]


def _published(api, w: dict) -> dict:
    return api("ana").get(f"/api/subject-areas/{w['canvas']}")


def _stored(fake_db, w: dict) -> dict:
    return fake_db.raw["subject_areas"].find_one({"_id": w["canvas"]})


# ── round-trip ──────────────────────────────────────────────────────────


def test_el_trazo_viaja_del_draft_a_produccion(api, world):
    ana = api("ana")
    w = world
    cs = _draft(api, w)
    ana.change(cs, "subject_areas", w["canvas"], _canvas(w, routes={w["rel"]: ROUTE}))

    # draft: canvas del draft y estado efectivo ya traen el trazo
    draft = ana.get(f"/api/subject-areas/{w['canvas']}/diagram?changesetId={cs}")
    assert draft["subjectArea"]["routes"] == {w["rel"]: ROUTE}
    eff = ana.get(f"/api/changesets/{cs}/effective/subject_areas?ids={w['canvas']}")
    assert eff[0]["routes"] == {w["rel"]: ROUTE}

    # producción intacta hasta aprobar
    assert _published(api, w)["routes"] == {}
    assert ana.get(f"/api/subject-areas/{w['canvas']}/diagram")["subjectArea"]["routes"] == {}

    publish(api, cs)

    assert _published(api, w)["routes"] == {w["rel"]: ROUTE}
    assert ana.get(f"/api/subject-areas/{w['canvas']}/diagram")["subjectArea"]["routes"] == {w["rel"]: ROUTE}
    listed = ana.get(f"/api/projects/{w['pid']}/subject-areas")
    assert [c["routes"] for c in listed if c["id"] == w["canvas"]] == [{w["rel"]: ROUTE}]
    # un draft nuevo parte del trazo publicado
    cs2 = _draft(api, w)
    eff = ana.get(f"/api/changesets/{cs2}/effective/subject_areas?ids={w['canvas']}")
    assert eff[0]["routes"] == {w["rel"]: ROUTE}


def test_el_trazo_no_cambia_nada_mas_del_canvas(api, world):
    ana = api("ana")
    w = world
    before = _published(api, w)
    cs = _draft(api, w)
    ana.change(cs, "subject_areas", w["canvas"], _canvas(w, routes={w["rel"]: ROUTE}))
    publish(api, cs)
    after = _published(api, w)
    assert {k: v for k, v in after.items() if k != "routes"} == {k: v for k, v in before.items() if k != "routes"}
    dia = ana.get(f"/api/subject-areas/{w['canvas']}/diagram")
    assert [r["id"] for r in dia["relationships"]] == [w["rel"]]      # la relación no se tocó
    assert sorted(t["id"] for t in dia["tables"]) == sorted([w["t1"], w["t2"]])


def test_quitar_el_trazo_vuelve_al_camino_automatico(api, world):
    ana = api("ana")
    w = world
    cs = _draft(api, w)
    ana.change(cs, "subject_areas", w["canvas"], _canvas(w, routes={w["rel"]: ROUTE}))
    publish(api, cs)
    cs2 = _draft(api, w)
    ana.change(cs2, "subject_areas", w["canvas"], _canvas(w, routes={}))
    publish(api, cs2)
    assert _published(api, w)["routes"] == {}


# ── revisión ─────────────────────────────────────────────────────────────


def test_change_details_no_muestra_el_trazo(api, world):
    ana, beto = api("ana"), api("beto")
    w = world
    cs = _draft(api, w)
    ana.change(cs, "subject_areas", w["canvas"], _canvas(w, routes={w["rel"]: ROUTE}))
    ana.post(f"/api/changesets/{cs}/submit", {"title": "trazos", "reviewers": ["beto"]})
    details = beto.post(f"/api/changesets/{cs}/diff/details",
                        {"items": [{"collection": "subject_areas", "entityId": w["canvas"]}]})
    assert details["items"][0]["action"] == "modified"
    assert details["items"][0]["fields"] == []


# ── validación de entrada ────────────────────────────────────────────────


def test_un_trazo_malformado_da_422_y_no_se_graba(api, world):
    ana = api("ana")
    w = world
    cs = _draft(api, w)
    status, body = ana.change(cs, "subject_areas", w["canvas"],
                              _canvas(w, routes={w["rel"]: [{"x": "a", "y": 2}]}), expect=422)
    assert status == 422 and "routes" in str(body)
    status, body = ana.bulk(cs, [{"collection": "subject_areas", "entityId": w["canvas"], "op": "upsert",
                                  "payload": _canvas(w, routes={w["rel"]: [{"x": 1, "y": 2}] * 33})}], expect=422)
    assert status == 422 and "at most" in str(body)
    assert ana.get(f"/api/changesets/{cs}/effective/subject_areas?ids={w['canvas']}")[0]["routes"] == {}


# ── rollback ─────────────────────────────────────────────────────────────


def test_rollback_devuelve_el_trazo_anterior(api, world):
    ana = api("ana")
    w = world
    cs = _draft(api, w)
    ana.change(cs, "subject_areas", w["canvas"], _canvas(w, routes={w["rel"]: ROUTE}))
    v3 = publish(api, cs)
    cs2 = _draft(api, w)
    ana.change(cs2, "subject_areas", w["canvas"], _canvas(w, routes={w["rel"]: ROUTE_2}))
    publish(api, cs2)
    assert _published(api, w)["routes"] == {w["rel"]: ROUTE_2}

    restore = ana.post(f"/api/changesets/{v3['id']}/rollback")
    publish(api, restore["id"], title="Restore v3")
    assert _published(api, w)["routes"] == {w["rel"]: ROUTE}


def test_rollback_quita_el_trazo_de_un_canvas_previo_al_doc_99(api, world, fake_db):
    """El canvas publicado NO trae la llave `routes` (todo lo migrado antes del
    doc 99). El apply es un $set (merge): sin la llave explícita, la imagen
    previa sin `routes` no podría quitar el trazo agregado después."""
    ana = api("ana")
    w = world
    fake_db.raw["subject_areas"].update_one({"_id": w["canvas"]}, {"$unset": {"routes": ""}})
    assert "routes" not in _stored(fake_db, w)
    assert _published(api, w)["routes"] == {}              # se lee como «sin trazos»

    cs = _draft(api, w)
    ana.change(cs, "subject_areas", w["canvas"], _canvas(w, routes={w["rel"]: ROUTE}))
    publish(api, cs)
    assert _published(api, w)["routes"] == {w["rel"]: ROUTE}

    restore = ana.post(f"/api/changesets/{w['v2']['id']}/rollback")
    publish(api, restore["id"], title="Restore v2")
    assert _published(api, w)["routes"] == {}
    assert _stored(fake_db, w)["routes"] == {}


# ── concurrencia: dos drafts sobre el mismo canvas (opción A) ─────────────


def test_un_draft_sin_la_llave_routes_no_publica_trazos_ajenos(api, world):
    """Estado mezclado: Carla publica un trazo; Ana, que movió una tabla con un
    cliente que no manda `routes`, publica después. Gana el canvas de Ana
    COMPLETO — sus posiciones con SUS trazos (ninguno) — y no sus posiciones
    con los trazos de Carla, que ya no calzarían con los bloques."""
    ana, carla = api("ana"), api("carla")
    w = world
    cs_ana = _draft(api, w, "ana")
    cs_carla = _draft(api, w, "carla")
    moved = {w["t1"]: {"x": 0, "y": 0}, w["t2"]: {"x": 900, "y": 300}}
    legacy = _canvas(w, layout=moved)
    assert "routes" not in legacy
    ana.change(cs_ana, "subject_areas", w["canvas"], legacy)
    carla.change(cs_carla, "subject_areas", w["canvas"], _canvas(w, routes={w["rel"]: ROUTE}))

    publish(api, cs_carla, owner="carla")
    assert _published(api, w)["routes"] == {w["rel"]: ROUTE}
    publish(api, cs_ana, owner="ana")

    out = _published(api, w)
    assert out["layout"] == moved
    assert out["routes"] == {}


def test_dos_drafts_con_trazos_gana_el_ultimo_completo_y_el_diff_avisa(api, world):
    ana, carla = api("ana"), api("carla")
    w = world
    cs_ana = _draft(api, w, "ana")
    cs_carla = _draft(api, w, "carla")
    ana_layout = {w["t1"]: {"x": 0, "y": 0}, w["t2"]: {"x": 900, "y": 300}}
    ana.change(cs_ana, "subject_areas", w["canvas"], _canvas(w, layout=ana_layout, routes={w["rel"]: ROUTE_2}))
    carla.change(cs_carla, "subject_areas", w["canvas"], _canvas(w, routes={w["rel"]: ROUTE}))
    publish(api, cs_carla, owner="carla")

    # Ana ve el aviso de conflicto sobre el canvas antes de publicar (no bloquea).
    diff = ana.get(f"/api/changesets/{cs_ana}/diff")
    flat = str(diff)
    assert w["canvas"] in flat and "'conflict': True" in flat

    publish(api, cs_ana, owner="ana")
    out = _published(api, w)
    assert out["layout"] == ana_layout and out["routes"] == {w["rel"]: ROUTE_2}


# ── cascadas que re-arman el canvas ───────────────────────────────────────


def test_borrar_una_tabla_conserva_los_trazos_del_canvas(api, world):
    """Borrar una tabla cascadea su membresía: el backend re-graba el canvas sin
    ella. Los trazos viajan intactos en ese upsert (los que quedan huérfanos se
    ignoran al dibujar y los poda el front)."""
    ana = api("ana")
    w = world
    routes = {w["rel"]: ROUTE, "subsym-otro": ROUTE_2}
    cs = _draft(api, w)
    ana.change(cs, "subject_areas", w["canvas"], _canvas(w, routes=routes))
    publish(api, cs)

    cs2 = _draft(api, w)
    ana.bulk(cs2, [
        {"collection": "views", "entityId": w["view"], "op": "delete"},
        {"collection": "relationships", "entityId": w["rel"], "op": "delete"},
        {"collection": "canonical_columns", "entityId": w["c_c"], "op": "delete"},
    ])
    ana.change(cs2, "canonical_tables", w["t2"], None, op="delete")
    eff = ana.get(f"/api/changesets/{cs2}/effective/subject_areas?ids={w['canvas']}")[0]
    assert eff["tableIds"] == [w["t1"]]
    assert eff["routes"] == routes
    publish(api, cs2)
    out = _published(api, w)
    assert out["tableIds"] == [w["t1"]] and out["routes"] == routes


# ── lectura tolerante ────────────────────────────────────────────────────


def test_un_canvas_con_trazo_corrupto_abre_y_no_bloquea_a_nadie(api, world, fake_db):
    """Dato corrupto en la BD (edición a mano): el canvas, el árbol del proyecto
    y el diagrama abren igual; el cliente recibe sólo los trazos sanos — así lo
    que devuelve al guardar pasa la validación — y quien borra una tabla puede
    publicar aunque la cascada re-arme ese canvas."""
    ana = api("ana")
    w = world
    fake_db.raw["subject_areas"].update_one({"_id": w["canvas"]}, {"$set": {"routes": {
        w["rel"]: ROUTE, "roto": [{"x": "a", "y": 2}], "raro": "texto"}}})

    assert _published(api, w)["routes"] == {w["rel"]: ROUTE}
    assert ana.get(f"/api/subject-areas/{w['canvas']}/diagram")["subjectArea"]["routes"] == {w["rel"]: ROUTE}
    assert len(ana.get(f"/api/projects/{w['pid']}/subject-areas")) == 1

    cs = _draft(api, w)
    eff = ana.get(f"/api/changesets/{cs}/effective/subject_areas?ids={w['canvas']}")[0]
    assert eff["routes"] == {w["rel"]: ROUTE}
    draft = ana.get(f"/api/subject-areas/{w['canvas']}/diagram?changesetId={cs}")
    assert draft["subjectArea"]["routes"] == {w["rel"]: ROUTE}
    # el cliente devuelve lo que leyó + su cambio: pasa
    ana.change(cs, "subject_areas", w["canvas"], {**eff, "layout": {**eff["layout"], w["t2"]: {"x": 5, "y": 5}}})

    # cascada de membresía sobre el canvas corrupto (otro draft): publica igual
    fake_db.raw["subject_areas"].update_one({"_id": w["canvas"]}, {"$set": {"routes": {
        w["rel"]: ROUTE, "roto": [{"x": "a", "y": 2}]}}})
    cs2 = _draft(api, w, "carla")
    carla = api("carla")
    carla.change(cs2, "views", w["view"], None, op="delete")
    publish(api, cs2, owner="carla")
    out = _stored(fake_db, w)
    assert out["viewIds"] == []
    assert out["routes"] == {w["rel"]: ROUTE}             # saneado al publicar


# ── Hallazgos de la revisión independiente (2026-09-28) ─────────────────────


def test_un_entero_gigante_da_422_no_500(api, world):
    """La lectura tolerante del mismo valor la cubre la prueba unitaria del
    modelo (la BD falsa no guarda enteros de más de 8 bytes)."""
    ana = api("ana")
    w = world
    cs = _draft(api, w)
    body = ('{"collection":"subject_areas","entityId":"%s","op":"upsert","payload":{"projectId":"%s",'
            '"name":"Modelo Clientes","tableIds":[],"layout":{},"routes":{"r":[{"x":%s,"y":1}]}}}'
            % (w["canvas"], w["pid"], "9" * 400))
    r = ana.client.put(f"/api/changesets/{cs}/changes", content=body,
                       headers={"X-Dev-User": "ana", "Content-Type": "application/json"})
    assert r.status_code == 422, r.text
    assert "out of range" in r.text
    assert ana.get(f"/api/changesets/{cs}/effective/subject_areas?ids={w['canvas']}")[0]["routes"] == {}


def test_effective_siempre_trae_routes_en_un_canvas(api, world, fake_db):
    """Un canvas anterior al doc 99 no tiene la llave: `effective` la devuelve
    vacía, igual que `diagram` y `GET /subject-areas/{id}`."""
    ana = api("ana")
    w = world
    fake_db.raw["subject_areas"].update_one({"_id": w["canvas"]}, {"$unset": {"routes": ""}})
    cs = _draft(api, w)
    eff = ana.get(f"/api/changesets/{cs}/effective/subject_areas?ids={w['canvas']}")
    assert eff[0]["routes"] == {}
    assert all("routes" in c for c in ana.get(f"/api/changesets/{cs}/effective/subject_areas"))
    # un canvas NUEVO del draft, grabado sin la llave
    ana.change(cs, "subject_areas", "sa-nuevo", {"projectId": w["pid"], "name": "Nuevo", "tableIds": [], "layout": {}})
    nuevo = ana.get(f"/api/changesets/{cs}/effective/subject_areas?ids=sa-nuevo")
    assert nuevo[0]["routes"] == {}


def test_una_llave_con_punto_hacia_routes_da_422(api, world):
    ana = api("ana")
    w = world
    cs = _draft(api, w)
    payload = {**_canvas(w, routes={w["rel"]: ROUTE}), "routes.evil": [{"x": "a"}]}
    status, body = ana.change(cs, "subject_areas", w["canvas"], payload, expect=422)
    assert status == 422 and "routes.evil" in str(body)
    status, body = ana.bulk(cs, [{"collection": "subject_areas", "entityId": w["canvas"], "op": "upsert",
                                  "payload": payload}], expect=422)
    assert status == 422
