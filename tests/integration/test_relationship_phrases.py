"""Frases de relación (doc 98) por HTTP, de punta a punta: la frase se escribe
en el draft, se ve en el canvas del draft, el revisor la ve en «Change
details», se publica y aparece en el canvas publicado, Links y Reporting.
Router → service → repositorio REALES; sólo el driver de BD es falso."""
from __future__ import annotations

from tests.integration.conftest import publish


def _rel_payload(w: dict, **over) -> dict:
    return {"parentTableId": w["t1"], "childTableId": w["t2"],
            "pairs": [{"parentColumnId": w["c_a"], "childColumnId": w["c_c"]}], **over}


def _only(rels: list[dict], rel_id: str) -> dict:
    hits = [r for r in rels if r["id"] == rel_id]
    assert len(hits) == 1, rels
    return hits[0]


def test_frases_viajan_del_draft_a_produccion(api, world):
    ana, beto = api("ana"), api("beto")
    w = world
    cs = ana.post("/api/changesets/snapshot", {"projectId": w["pid"]}, expect=201)["id"]
    ana.change(cs, "relationships", w["rel"], _rel_payload(
        w, parentToChildPhrase="  Cliente tiene ", childToParentPhrase="pertenece a un Cliente"))

    # ── draft: canvas y slice por tabla ya traen la frase NORMALIZADA ────
    draft = ana.get(f"/api/subject-areas/{w['canvas']}/diagram?changesetId={cs}")
    rel = _only(draft["relationships"], w["rel"])
    assert rel["parentToChildPhrase"] == "Cliente tiene"
    assert rel["childToParentPhrase"] == "pertenece a un Cliente"
    eff = ana.get(f"/api/changesets/{cs}/effective/relationships?tableId={w['t1']}")
    assert _only(eff, w["rel"])["parentToChildPhrase"] == "Cliente tiene"

    # ── producción intacta hasta aprobar ────────────────────────────────
    pub = ana.get(f"/api/subject-areas/{w['canvas']}/diagram")
    assert _only(pub["relationships"], w["rel"])["parentToChildPhrase"] is None

    # ── review: «Change details» muestra las dos frases con su etiqueta ──
    ana.post(f"/api/changesets/{cs}/submit", {"title": "frases", "reviewers": ["beto"]})
    details = beto.post(f"/api/changesets/{cs}/diff/details",
                        {"items": [{"collection": "relationships", "entityId": w["rel"]}]})
    fields = {f["key"]: f for f in details["items"][0]["fields"]}
    assert fields["parentToChildPhrase"]["label"] == "Parent-to-child phrase"
    assert fields["parentToChildPhrase"]["before"] is None
    assert fields["parentToChildPhrase"]["after"] == "Cliente tiene"
    assert fields["childToParentPhrase"]["after"] == "pertenece a un Cliente"
    # nada más cambió: ni pares ni cardinalidades ensucian el diff
    assert set(fields) == {"parentToChildPhrase", "childToParentPhrase"}
    beto.post(f"/api/changesets/{cs}/review", {"decision": "approve"})

    # ── producción ─────────────────────────────────────────────────────
    pub = ana.get(f"/api/subject-areas/{w['canvas']}/diagram")
    rel = _only(pub["relationships"], w["rel"])
    assert rel["parentToChildPhrase"] == "Cliente tiene"
    assert rel["childToParentPhrase"] == "pertenece a un Cliente"
    assert _only(ana.get(f"/api/relationships?tableId={w['t1']}"), w["rel"])["childToParentPhrase"] \
        == "pertenece a un Cliente"

    # Properties · Links (también lo que ve el Object Inspector)
    link = _only(ana.get(f"/api/relationships/links?tableId={w['t2']}")["links"], w["rel"])
    assert link["parentToChildPhrase"] == "Cliente tiene"
    assert link["childToParentPhrase"] == "pertenece a un Cliente"

    # Reporting: hoja Relationships (una fila por par) + Report Designer
    rows = ana.get(f"/api/reporting/insights/relationships?projectId={w['pid']}")
    row = _only(rows, w["rel"])
    assert row["parentToChildPhrase"] == "Cliente tiene"
    assert row["childToParentPhrase"] == "pertenece a un Cliente"
    q = ana.post("/api/reporting/query", {
        "projectId": w["pid"], "from": "relationships",
        "select": ["parentToChildPhrase", "childToParentPhrase"], "limit": 10})
    assert [(r["parentToChildPhrase"], r["childToParentPhrase"]) for r in q["rows"]] \
        == [("Cliente tiene", "pertenece a un Cliente")]


def test_borrar_la_frase_la_deja_en_null(api, world):
    ana = api("ana")
    w = world
    cs = ana.post("/api/changesets/snapshot", {"projectId": w["pid"]}, expect=201)["id"]
    ana.change(cs, "relationships", w["rel"], _rel_payload(w, parentToChildPhrase="Cliente tiene"))
    publish(api, cs)
    cs2 = ana.post("/api/changesets/snapshot", {"projectId": w["pid"]}, expect=201)["id"]
    ana.change(cs2, "relationships", w["rel"], _rel_payload(w, parentToChildPhrase=""))
    publish(api, cs2)
    pub = ana.get(f"/api/subject-areas/{w['canvas']}/diagram")
    assert _only(pub["relationships"], w["rel"])["parentToChildPhrase"] is None


def test_rollback_devuelve_la_frase_anterior(api, world):
    ana = api("ana")
    w = world
    cs = ana.post("/api/changesets/snapshot", {"projectId": w["pid"]}, expect=201)["id"]
    ana.change(cs, "relationships", w["rel"], _rel_payload(w, childToParentPhrase="pertenece a un Cliente"))
    v3 = publish(api, cs)
    cs2 = ana.post("/api/changesets/snapshot", {"projectId": w["pid"]}, expect=201)["id"]
    ana.change(cs2, "relationships", w["rel"], _rel_payload(w, childToParentPhrase="es de un Cliente"))
    publish(api, cs2)
    assert _only(ana.get(f"/api/relationships?tableId={w['t1']}"), w["rel"])["childToParentPhrase"] \
        == "es de un Cliente"

    # Rollback = draft inverso que se revisa y publica como cualquier otro.
    restore = ana.post(f"/api/changesets/{v3['id']}/rollback")
    publish(api, restore["id"], title="Restore v3")
    assert _only(ana.get(f"/api/relationships?tableId={w['t1']}"), w["rel"])["childToParentPhrase"] \
        == "pertenece a un Cliente"


def test_rollback_quita_la_frase_de_una_relacion_previa_al_doc_98(api, world, fake_db):
    """La relación publicada NO trae las llaves de frase (todo lo migrado antes
    del doc 98). El apply es un $set (merge): sin un default explícito, la
    imagen previa sin llaves no podría quitar la frase agregada después."""
    ana = api("ana")
    w = world
    fake_db.raw["relationships"].update_one(
        {"_id": w["rel"]}, {"$unset": {"parentToChildPhrase": "", "childToParentPhrase": ""}})
    stored = fake_db.raw["relationships"].find_one({"_id": w["rel"]})
    assert "parentToChildPhrase" not in stored and "childToParentPhrase" not in stored

    cs = ana.post("/api/changesets/snapshot", {"projectId": w["pid"]}, expect=201)["id"]
    ana.change(cs, "relationships", w["rel"], _rel_payload(w, parentToChildPhrase="Cliente tiene"))
    publish(api, cs)
    assert fake_db.raw["relationships"].find_one({"_id": w["rel"]})["parentToChildPhrase"] == "Cliente tiene"

    restore = ana.post(f"/api/changesets/{w['cs2']}/rollback")
    publish(api, restore["id"], title="Restore v2")
    assert fake_db.raw["relationships"].find_one({"_id": w["rel"]}).get("parentToChildPhrase") is None
    assert _only(ana.get(f"/api/relationships?tableId={w['t1']}"), w["rel"])["parentToChildPhrase"] is None
    pub = ana.get(f"/api/subject-areas/{w['canvas']}/diagram")
    assert _only(pub["relationships"], w["rel"])["parentToChildPhrase"] is None


def test_changeset_rechaza_una_frase_que_no_es_texto(api, world):
    ana = api("ana")
    w = world
    cs = ana.post("/api/changesets/snapshot", {"projectId": w["pid"]}, expect=201)["id"]
    for malo in (False, 0, {}, ["x"]):
        ana.change(cs, "relationships", w["rel"], _rel_payload(w, parentToChildPhrase=malo), expect=422)


def test_endpoint_directo_ya_no_escribe_frases(api, world, fake_db):
    """Doc 105: las relaciones se escriben SÓLO por una versión (las frases
    viajan y se normalizan por el changeset: tests de arriba)."""
    w = world
    body = {"parentTableId": w["t1"], "childTableId": w["t2"],
            "pairs": [{"parentColumnId": w["c_a"], "childColumnId": w["c_c"]}],
            "parentToChildPhrase": "Cliente origina"}
    api("ana").call("PUT", f"/api/relationships/{w['rel']}", body, expect=409)
    assert fake_db.raw["relationships"].find_one({"_id": w["rel"]}).get("parentToChildPhrase") is None
