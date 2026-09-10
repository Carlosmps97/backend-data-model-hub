"""Proyectos INDEPENDIENTES (doc 75 D1–D7, I2–I6) probados por HTTP: mismos
nombres físicos en dos proyectos, referencias cruzadas rechazadas, catálogo y
estándares acotados, rollback de estándares que no toca al vecino, compare
entre proyectos rechazado y borrado de proyecto por draft con cascada."""
from __future__ import annotations

from tests.integration.conftest import build_world, column_payload, publish, table_payload


def test_two_projects_do_not_see_each_other(api):
    a = build_world(api, "Proyecto A", prefix="a")
    b = build_world(api, "Proyecto B", prefix="b")          # mismo M_CLIENTE/M_CUENTA/STG: sin 409
    ana, carla, admin = api("ana"), api("carla"), api("admin")
    pa, pb = a["pid"], b["pid"]

    # Catálogo, esquemas, carpetas, canvases y buscador: sólo lo propio.
    assert {t["id"] for t in ana.get(f"/api/projects/{pa}/catalog/tables")} == {a["t1"], a["t2"]}
    assert {t["id"] for t in ana.get(f"/api/projects/{pb}/catalog/tables")} == {b["t1"], b["t2"]}
    assert {s["projectId"] for s in ana.get(f"/api/projects/{pa}/schemas")} == {pa}
    assert {f["projectId"] for f in ana.get(f"/api/projects/{pa}/folders")} == {pa}
    assert {sa["projectId"] for sa in ana.get(f"/api/projects/{pa}/subject-areas")} == {pa}
    hits = ana.get(f"/api/projects/{pa}/catalog/columns?q=CODCLIENTE")
    assert {h["tableId"] for h in hits} <= {a["t1"], a["t2"]}
    inv = ana.get(f"/api/projects/{pb}/catalog/inventory")
    assert {t["id"] for t in inv["tables"]} == {b["t1"], b["t2"]}
    # Versiones: por proyecto y global con proyecto por fila.
    assert {v["projectId"] for v in ana.get(f"/api/projects/{pa}/versions")} == {pa}
    assert {v["projectId"] for v in ana.get("/api/versions")} == {pa, pb}
    ana.get(f"/api/versions/compare?fromId={a['v1']}&toId={b['cs2']}", expect=409)

    # Guard anti-cruce (I2): un draft de B no puede referir entidades de A.
    cs = carla.post("/api/changesets/snapshot", {"projectId": pb}, expect=201)["id"]
    st, err = carla.change(cs, "canonical_columns", "x-col", column_payload(a["t1"], "AJENA", "Ajena", 5), expect=409)
    assert "another project" in str(err)
    carla.change(cs, "relationships", "x-rel", {"parentTableId": a["t1"], "childTableId": b["t2"],
                                                "pairs": [{"parentColumnId": a["c_a"], "childColumnId": b["c_c"]}]}, expect=409)
    carla.change(cs, "subject_areas", "x-sa", {"projectId": pb, "name": "Mixto", "tableIds": [a["t1"]]}, expect=409)
    carla.change(cs, "views", "x-view", {"name": "V_X", "sourceTableIds": [a["t1"]]}, expect=409)
    carla.change(cs, "projects", pa, None, op="delete", expect=409)               # sólo SU proyecto
    # El estampado server-side pisa un projectId ajeno mandado por el cliente (I1).
    carla.change(cs, "canonical_tables", "b-t3", {**table_payload("M_NUEVA", "Nueva"), "projectId": pa})
    eff = {t["id"]: t for t in carla.get(f"/api/changesets/{cs}/effective/canonical_tables")}
    assert eff["b-t3"]["projectId"] == pb
    # Unicidad POR proyecto: M_CLIENTE ya existe en A y en B → en B sí es duplicado.
    carla.change(cs, "canonical_tables", "b-t4", table_payload("M_CLIENTE", "Dup"), expect=409)


def test_standards_are_per_project_and_rollback_is_scoped(api):
    a = build_world(api, "Proyecto A", prefix="a")
    b = build_world(api, "Proyecto B", prefix="b")
    admin, ana = api("admin"), api("ana")
    pa, pb = a["pid"], b["pid"]
    admin.post(f"/api/projects/{pa}/standards/apply",
               {"kind": "domain", "domainsUpsert": [{"name": "Codigo", "defaultDataType": "VARCHAR(20)"}]})
    admin.post(f"/api/projects/{pb}/standards/apply",
               {"kind": "domain", "domainsUpsert": [{"name": "Codigo", "defaultDataType": "VARCHAR(30)"}]})
    admin.post(f"/api/projects/{pa}/standards/apply",
               {"kind": "glossary", "termsUpsert": [{"term": "Sucursal", "abbrev": "SUC", "scope": "column"}]})
    doms_a = {d["name"]: d for d in ana.get(f"/api/projects/{pa}/domains")}
    doms_b = {d["name"]: d for d in ana.get(f"/api/projects/{pb}/domains")}
    assert doms_a["Codigo"]["defaultDataType"] == "VARCHAR(20)" and doms_b["Codigo"]["defaultDataType"] == "VARCHAR(30)"
    assert doms_a["Codigo"]["id"] != doms_b["Codigo"]["id"]
    assert [t["abbrev"] for t in ana.get(f"/api/projects/{pa}/glossary")] == ["SUC"]
    assert ana.get(f"/api/projects/{pb}/glossary") == []
    assert [v["seq"] for v in ana.get(f"/api/projects/{pa}/standards/versions")] == [3, 2, 1]
    assert [v["seq"] for v in ana.get(f"/api/projects/{pb}/standards/versions")] == [2, 1]
    # UDPs y snapshot también por proyecto.
    assert ana.get(f"/api/projects/{pa}/standards/snapshot")["domains"][0]["name"] == "Codigo"
    ana.get(f"/api/projects/{pa}/udp")
    # Rollback de A a su seq 1 (vacío): A queda sin dominio ni término; B intacto (I5).
    admin.post(f"/api/projects/{pa}/standards/rollback", {"targetSeq": 1})
    assert ana.get(f"/api/projects/{pa}/domains") == [] and ana.get(f"/api/projects/{pa}/glossary") == []
    assert [d["defaultDataType"] for d in ana.get(f"/api/projects/{pb}/domains")] == ["VARCHAR(30)"]
    # Proyecto nuevo COPIANDO los estándares de B (D15): dominios con ids nuevos.
    c = admin.post("/api/projects", {"name": "Proyecto C", "copyFrom": {"projectId": pb, "blocks": ["domains"]}}, expect=201)
    doms_c = ana.get(f"/api/projects/{c['id']}/domains")
    assert [d["defaultDataType"] for d in doms_c] == ["VARCHAR(30)"] and doms_c[0]["id"] != doms_b["Codigo"]["id"]
    assert ana.get(f"/api/projects/{c['id']}/versions/published")["versionLabel"] == "v1"
    # Un proyecto inexistente/borrado responde 404 uniforme en toda ruta de alcance.
    ana.get("/api/projects/no-such/domains", expect=404)


def test_delete_project_through_a_draft_cascades_and_isolates(api):
    a = build_world(api, "Proyecto A", prefix="a")
    b = build_world(api, "Proyecto B", prefix="b")
    ana, carla, beto = api("ana"), api("carla"), api("beto")
    pa, pb = a["pid"], b["pid"]
    other_draft = carla.post("/api/changesets/snapshot", {"projectId": pb}, expect=201)["id"]   # draft ajeno abierto en B

    cs = ana.post("/api/changesets/snapshot", {"projectId": pb}, expect=201)["id"]
    ana.change(cs, "projects", pb, None, op="delete")
    ana.post(f"/api/changesets/{cs}/submit", {"title": "Borrar B", "reviewers": ["beto"]})
    row = next(r for r in beto.get("/api/requests?reviewer=beto") if r["id"] == cs)
    assert row["deletesProject"] is True
    diff = beto.get(f"/api/changesets/{cs}/diff")
    assert diff["impact"]["deletesProject"]["name"] == "Proyecto B"
    assert diff["impact"]["deletesProject"]["counts"]["tables"] == 2
    approved = beto.post(f"/api/changesets/{cs}/review", {"decision": "approve"})
    assert approved["status"] == "approved"

    # B desapareció de la web; A sigue intacto.
    assert [p["name"] for p in ana.get("/api/projects")] == ["Proyecto A"]
    ana.get(f"/api/projects/{pb}/versions", expect=404)
    ana.get(f"/api/projects/{pb}/catalog/tables", expect=404)
    ana.post("/api/changesets/snapshot", {"projectId": pb}, expect=404)
    carla.change(other_draft, "canonical_tables", "b-t9", table_payload("M_TARDE", "Tarde"), expect=409)   # project-deleted
    assert ana.get(f"/api/catalog/tables/{b['t1']}/columns") == []                      # soft-delete en cascada
    assert len(ana.get(f"/api/catalog/tables/{a['t1']}/columns")) == 2
    assert {t["id"] for t in ana.get(f"/api/projects/{pa}/catalog/tables")} == {a["t1"], a["t2"]}
    assert ana.get(f"/api/projects/{pa}/versions/published")["id"] == a["cs2"]
    # La historia de B se conserva (versiones/ledger no se borran): la fila sigue en la lista global.
    assert any(v["id"] == cs for v in ana.get("/api/versions"))
