"""Flujo completo de versionado de UN proyecto por HTTP (doc 82): crear
proyecto → snapshot → cambios → submit → bandeja → diff → «Change details» →
approve → producción → historial → compare → segunda versión → rollback →
snapshot `asof:` → withdraw/reject/reopen. Cada paso pasa por el router, el
service y el repositorio REALES; sólo el driver de BD es falso."""
from __future__ import annotations

from tests.integration.conftest import build_world, column_payload, publish, table_payload


def test_publish_flow_end_to_end(api):
    ana, beto, admin = api("ana"), api("beto"), api("admin")

    # ── proyecto nuevo: nace con v1 y estándares vacíos ──────────────────
    p = admin.post("/api/projects", {"name": "P1"}, expect=201)
    pid = p["id"]
    admin.post("/api/projects", {"name": "p1"}, expect=409)              # nombre único (CI)
    versions = ana.get(f"/api/projects/{pid}/versions")
    assert [v["versionLabel"] for v in versions] == ["v1"] and versions[0]["projectId"] == pid
    assert ana.get(f"/api/projects/{pid}/versions/published")["versionLabel"] == "v1"
    assert ana.get(f"/api/projects/{pid}/standards/versions")[0]["seq"] == 1

    # ── snapshot desde producción ──────────────────────────────────────
    cs = ana.post("/api/changesets/snapshot", {"projectId": pid}, expect=201)
    cs_id = cs["id"]
    assert cs["versionLabel"] == "v2" and cs["projectId"] == pid and cs["status"] == "draft"
    ana.post("/api/changesets/snapshot", {"projectId": "no-such-project"}, expect=404)

    # ── cambios (single + bulk) ────────────────────────────────────────
    ana.change(cs_id, "schemas", "sch-1", {"name": "STG", "kind": "tables"})
    ana.change(cs_id, "folders", "fld-1", {"projectId": pid, "name": "Clientes"})
    ana.change(cs_id, "canonical_tables", "t1", table_payload("M_CLIENTE", "Cliente"))
    ana.change(cs_id, "canonical_columns", "c-a", column_payload("t1", "CODCLIENTE", "Codigo", 0, pk=True))
    ana.change(cs_id, "canonical_columns", "c-b", column_payload("t1", "NBRCLIENTE", "Nombre", 1))
    # duplicados dentro del proyecto → 409 legible; otro owner → 403; payload inválido → 422
    st, err = ana.change(cs_id, "canonical_tables", "t-dup", table_payload("m_cliente", "Otra"), expect=409)
    assert "already exists" in str(err)
    api("carla").change(cs_id, "canonical_tables", "t9", table_payload("X", "X"), expect=403)
    ana.change(cs_id, "canonical_columns", "c-bad", {"physicalName": "SIN_TABLA"}, expect=422)
    ana.bulk(cs_id, [
        {"collection": "canonical_tables", "entityId": "t2", "op": "upsert", "payload": table_payload("M_CUENTA", "Cuenta")},
        {"collection": "canonical_columns", "entityId": "c-c", "op": "upsert",
         "payload": column_payload("t2", "CODCLIENTE", "Codigo", 0, isForeignKey=True)},
    ])
    ana.change(cs_id, "relationships", "r1", {"parentTableId": "t1", "childTableId": "t2",
                                              "pairs": [{"parentColumnId": "c-a", "childColumnId": "c-c"}]})
    ana.change(cs_id, "views", "v1", {"name": "V_CLIENTE", "schema": "STG_VU", "sourceTableIds": ["t1"],
                                      "sources": [{"column": "CODCLIENTE", "tableId": "t1"}], "showOnCanvas": True})
    ana.change(cs_id, "subject_areas", "sa-1", {"projectId": pid, "folderId": "fld-1", "name": "Modelo Clientes",
                                                "tableIds": ["t1", "t2"], "viewIds": ["v1"],
                                                "layout": {"t1": {"x": 0, "y": 0}}})

    # ── lecturas draft-aware (canvas, explorer, buscador) ──────────────
    eff = ana.get(f"/api/changesets/{cs_id}/effective/canonical_tables")
    assert {t["physicalName"] for t in eff} == {"M_CLIENTE", "M_CUENTA"} and all(t["projectId"] == pid for t in eff)
    assert len(ana.get(f"/api/changesets/{cs_id}/effective/canonical_columns?tableId=t1")) == 2
    inv = ana.get(f"/api/projects/{pid}/catalog/inventory?changesetId={cs_id}")
    assert {t["physicalName"] for t in inv["tables"]} == {"M_CLIENTE", "M_CUENTA"}
    diagram = ana.get(f"/api/subject-areas/sa-1/diagram?changesetId={cs_id}")
    assert len(diagram["tables"]) == 2 and len(diagram["relationships"]) == 1 and len(diagram["views"]) == 1
    assert ana.get(f"/api/projects/{pid}/catalog/search?q=CLIENTE&changesetId={cs_id}")
    assert ana.get(f"/api/changesets/{cs_id}/schemas/sch-1/impact") == {"name": "STG", "tables": 2, "views": 0, "canvases": 1}
    # producción sigue vacía: nada se publica antes de aprobar
    assert ana.get(f"/api/projects/{pid}/catalog/tables") == []

    # ── submit → bandeja → diff → «Change details» → approve ───────────
    ana.post(f"/api/changesets/{cs_id}/submit", {"title": "v2", "reviewers": []}, expect=400)
    ana.post(f"/api/changesets/{cs_id}/submit", {"title": "v2", "reviewers": ["diego"]}, expect=400)   # sin review.decide
    sub = ana.post(f"/api/changesets/{cs_id}/submit", {"title": "Alta clientes", "reviewers": ["beto"]})
    assert sub["status"] == "submitted"
    ana.change(cs_id, "canonical_tables", "t3", table_payload("M_TARDE", "Tarde"), expect=409)      # locked
    inbox = beto.get("/api/requests?reviewer=beto")
    assert [r["id"] for r in inbox] == [cs_id] and inbox[0]["projectId"] == pid and inbox[0]["deletesProject"] is False
    diff = beto.get(f"/api/changesets/{cs_id}/diff")
    assert {e["name"] for e in diff["collections"]["canonical_tables"]["added"]} == {"M_CLIENTE", "M_CUENTA"}
    assert diff["impact"]["tablesTouched"] == 2 and diff["impact"]["columnsTouched"] == 3
    assert diff["tree"][0]["id"] == pid and diff["tree"][0]["children"][0]["children"][0]["name"] == "Modelo Clientes"
    assert {s["kind"] for s in diff["structure"]} == {"Folder", "Canvas", "Schema"}
    items = [{"collection": c, "entityId": e} for c, e in [
        ("schemas", "sch-1"), ("folders", "fld-1"), ("subject_areas", "sa-1"), ("canonical_tables", "t1"),
        ("canonical_columns", "c-a"), ("canonical_columns", "c-c"), ("relationships", "r1"), ("views", "v1")]]
    details = beto.post(f"/api/changesets/{cs_id}/diff/details", {"items": items + [{"collection": "views", "entityId": "ajena"}]})
    assert len(details["items"]) == len(items)                        # la ajena al changeset se omite
    rel = next(i for i in details["items"] if i["collection"] == "relationships")
    assert "M_CLIENTE" in str(rel) and "M_CUENTA" in str(rel)          # nombres resueltos desde el draft
    beto.post(f"/api/changesets/{cs_id}/diff/details", {"items": [{"collection": "nope", "entityId": "x"}]}, expect=400)
    api("diego").post(f"/api/changesets/{cs_id}/review", {"decision": "approve"}, expect=403)   # sin permiso
    api("admin").post(f"/api/changesets/{cs_id}/review", {"decision": "approve"}, expect=403)   # no asignado
    approved = beto.post(f"/api/changesets/{cs_id}/review", {"decision": "approve"})
    assert approved["status"] == "approved" and approved["appliedAt"] and approved["reviewedBy"] == "beto"

    # ── producción ─────────────────────────────────────────────────────
    assert ana.get(f"/api/projects/{pid}/versions/published")["id"] == cs_id
    tables = ana.get(f"/api/projects/{pid}/catalog/tables")
    assert {t["physicalName"] for t in tables} == {"M_CLIENTE", "M_CUENTA"} and all(t["projectId"] == pid for t in tables)
    assert [c["physicalName"] for c in ana.get("/api/catalog/tables/t1/columns")] == ["CODCLIENTE", "NBRCLIENTE"]
    pub_diagram = ana.get("/api/subject-areas/sa-1/diagram")
    assert len(pub_diagram["tables"]) == 2 and len(pub_diagram["relationships"]) == 1 and len(pub_diagram["views"]) == 1
    assert [f["name"] for f in ana.get(f"/api/projects/{pid}/folders")] == ["Clientes"]
    assert [s["name"] for s in ana.get(f"/api/projects/{pid}/schemas")] == ["STG"]
    assert ana.get(f"/api/summary?projectId={pid}")
    inspect = ana.get("/api/catalog/inspect/tables/t1")
    assert inspect["table"]["physicalName"] == "M_CLIENTE"

    # ── historial + compare v1→v2 ──────────────────────────────────────
    hist = ana.get("/api/changesets/history/canonical_tables/t1")["items"]
    assert hist[0]["action"] == "created" and hist[0]["version"] == "v2" and hist[0]["user"]["id"] == "ana"
    v1_id = versions[0]["id"]
    cmp = ana.get(f"/api/versions/compare?fromId={cs_id}&toId={v1_id}")          # cualquier orden
    assert cmp["from"]["id"] == v1_id and cmp["to"]["id"] == cs_id and cmp["counts"]["added"] == 10
    cmp_details = ana.post("/api/versions/compare/details",
                           {"fromId": v1_id, "toId": cs_id, "items": [{"collection": "canonical_columns", "entityId": "c-a"}]})
    assert cmp_details["items"][0]["name"] == "CODCLIENTE"

    # ── v3: editar, borrar y renombrar sobre lo publicado ───────────────
    cs3 = ana.post("/api/changesets/snapshot", {"projectId": pid}, expect=201)["id"]
    ana.change(cs3, "canonical_tables", "t1", table_payload("M_CLIENTE", "Cliente", description="Editada en v3"))
    ana.change(cs3, "canonical_columns", "c-b", column_payload("t1", "NBRCLIENTECOMPLETO", "Nombre completo", 1))
    ana.change(cs3, "canonical_columns", "c-c", None, op="delete")
    ana.change(cs3, "relationships", "r1", None, op="delete")
    v3 = publish(api, cs3, title="Ajustes v3")
    assert v3["versionLabel"] == "v3"
    assert [c["physicalName"] for c in ana.get("/api/catalog/tables/t1/columns")] == ["CODCLIENTE", "NBRCLIENTECOMPLETO"]
    assert ana.get("/api/catalog/tables/t2/columns") == []
    hist_t1 = ana.get("/api/changesets/history/canonical_tables/t1")["items"]
    assert [h["action"] for h in hist_t1] == ["edited", "created"]
    assert any(f["key"] == "description" for f in hist_t1[0]["fields"])
    hist_cc = ana.get("/api/changesets/history/canonical_columns/c-c")["items"]
    assert [h["action"] for h in hist_cc] == ["deleted", "created"]
    cmp13 = ana.get(f"/api/versions/compare?fromId={v1_id}&toId={cs3}")
    assert "c-c" not in {e["id"] for e in cmp13["collections"].get("canonical_columns", {}).get("added", [])}   # neto cero

    # ── snapshot `asof:` v2 = el modelo tal como quedó en v2 ───────────
    asof_cols = ana.get(f"/api/changesets/asof:{cs_id}/effective/canonical_columns?tableId=t1")
    assert [c["physicalName"] for c in asof_cols] == ["CODCLIENTE", "NBRCLIENTE"]
    assert len(ana.get(f"/api/changesets/asof:{cs_id}/effective/canonical_columns?tableId=t2")) == 1
    ana.get("/api/changesets/asof:no-such/effective/canonical_tables", expect=404)

    # ── rollback a v2 = draft inverso que se revisa como cualquier otro ──
    api("diego").post(f"/api/changesets/{cs_id}/rollback", expect=403)
    ana.post(f"/api/changesets/{cs3}/rollback", expect=409)                       # ya es producción
    draft4 = ana.post(f"/api/changesets/{cs_id}/rollback")
    assert draft4["status"] == "draft" and draft4["versionLabel"] == "v4" and draft4["restoredFrom"]["csId"] == cs_id
    assert [c["physicalName"] for c in ana.get(f"/api/changesets/{draft4['id']}/effective/canonical_columns?tableId=t1")] \
        == ["CODCLIENTE", "NBRCLIENTE"]
    publish(api, draft4["id"], title="Restore v2")
    assert [c["physicalName"] for c in ana.get("/api/catalog/tables/t1/columns")] == ["CODCLIENTE", "NBRCLIENTE"]
    assert len(ana.get("/api/catalog/tables/t2/columns")) == 1
    assert ana.get(f"/api/versions/compare?fromId={cs_id}&toId={draft4['id']}")["counts"]["total"] == 0   # v2 ≡ v4
    rows = ana.get("/api/versions")
    assert next(r for r in rows if r["id"] == draft4["id"])["restoredFrom"]["versionLabel"] == "v2"

    # ── withdraw / reject / reopen ─────────────────────────────────────
    cs5 = ana.post("/api/changesets/snapshot", {"projectId": pid}, expect=201)["id"]
    ana.change(cs5, "canonical_tables", "t5", table_payload("M_TEMP", "Temp"))
    ana.post(f"/api/changesets/{cs5}/submit", {"reviewers": ["beto"]})
    api("carla").post(f"/api/changesets/{cs5}/withdraw", expect=403)
    assert ana.post(f"/api/changesets/{cs5}/withdraw")["status"] == "draft"
    beto.post(f"/api/changesets/{cs5}/review", {"decision": "approve"}, expect=409)   # ya no está en revisión
    ana.post(f"/api/changesets/{cs5}/submit", {"reviewers": ["beto"]})
    beto.post(f"/api/changesets/{cs5}/review", {"decision": "reject"}, expect=422)          # doc 88 §5: motivo obligatorio
    rejected = beto.post(f"/api/changesets/{cs5}/review", {"decision": "reject", "note": "no"})
    # Doc 88 §6: el rechazo devuelve la versión DIRECTO a draft; la solicitud
    # queda en el historial con su motivo (y el ciclo retirado antes, también).
    assert rejected["status"] == "draft" and rejected["reviewNote"] == "no" and rejected["submittedAt"] is None
    assert [r["outcome"] for r in rejected["requests"]] == ["withdrawn", "rejected"]
    assert rejected["requests"][-1]["note"] == "no" and rejected["requests"][-1]["decidedBy"] == "beto"
    assert rejected["requests"][-1]["decisions"]["beto"]["status"] == "rejected"
    ana.post(f"/api/changesets/{cs5}/reopen", expect=409)                                     # ya es draft: nada que reabrir
    assert next(r for r in ana.get("/api/versions") if r["id"] == cs5)["lastRequest"]["outcome"] == "rejected"
    assert ana.get(f"/api/projects/{pid}/catalog/tables?q=TEMP") == []           # nunca llegó a producción
    assert ana.post(f"/api/changesets/{cs5}/comments", {"text": "hola"})["comments"][0]["author"] == "ana"


def test_world_fixture_publishes_v2(api):
    w = build_world(api)
    assert w["v2"]["versionLabel"] == "v2"
    assert len(api("ana").get(f"/api/projects/{w['pid']}/catalog/tables")) == 2
