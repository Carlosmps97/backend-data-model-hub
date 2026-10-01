"""Endpoints DIRECTOS (fuera del changeset) por HTTP (doc 82): cada uno
atraviesa router → service → repositorio reales con el proyecto derivado
server-side (doc 75 I1). Un 5xx acá es un bug de cableado.

Doc 105: las ESCRITURAS directas del modelo (canvases, carpetas, esquemas,
catálogo, relaciones, vistas) quedaron cerradas — 409, ver
`test_direct_writes_doc105.py` —; acá se ejercitan las LECTURAS directas que
siguen vivas, sobre el mundo publicado por el camino versionado."""
from __future__ import annotations

from tests.integration.conftest import table_payload


def test_structure_reads(api, world):
    ana = api("ana")
    pid, t1 = world["pid"], world["t1"]
    assert {s["name"] for s in ana.get(f"/api/projects/{pid}/schemas")} == {"STG"}
    got = ana.get(f"/api/folders/{world['folder']}")
    assert got["name"] == "Dominio Clientes" and got["projectId"] == pid
    assert len(ana.get(f"/api/folders?projectId={pid}")) == 1
    assert len(ana.get(f"/api/projects/{pid}/folders")) == 1
    sa = ana.get(f"/api/subject-areas/{world['canvas']}")
    assert sa["name"] == "Modelo Clientes" and sa["viewIds"] == [world["view"]]
    diagram = ana.get(f"/api/subject-areas/{world['canvas']}/diagram")
    assert {t["id"] for t in diagram["tables"]} == {t1, world["t2"]}
    assert len(ana.get(f"/api/projects/{pid}/subject-areas")) == 1
    ana.get("/api/subject-areas/no-such-canvas", expect=404)
    ana.get("/api/folders/no-such-folder", expect=404)


def test_catalog_views_and_relationships_reads(api, world):
    ana = api("ana")
    pid, t1, c_a = world["pid"], world["t1"], world["c_a"]
    assert [c["physicalName"] for c in ana.get(f"/api/catalog/tables/{t1}/columns")] == ["CODCLIENTE", "NBRCLIENTE"]
    assert {t["physicalName"] for t in ana.get(f"/api/projects/{pid}/catalog/tables?q=CLIENTE")} == {"M_CLIENTE"}
    assert ana.get(f"/api/relationships/links?tableId={t1}") is not None
    assert ana.get(f"/api/relationships?tableId={t1}")
    assert ana.get(f"/api/relationships/impact?columnId={c_a}") is not None
    assert {v["name"] for v in ana.get(f"/api/views?projectId={pid}")} == {"V_CLIENTE"}
    assert ana.get(f"/api/views/for-table?tableId={t1}")
    assert ana.get(f"/api/projects/{pid}/catalog/views/{world['view']}/columns") is not None


STANDARDS_VERSIONED = ("Standards changes go through Data Standards (Save & apply) so they keep "
                       "a version and can be rolled back.")


def _whole_db(raw) -> dict:
    """Foto de TODA la BD en memoria (auditoría incluida)."""
    return {c: sorted(raw[c].find({}), key=lambda d: str(d["_id"])) for c in raw.list_collection_names()}


def test_standards_direct_endpoints(api, world, fake_db):
    """Doc 105 (D1b): los estándares se cambian SÓLO por Data Standards (apply
    versionado, con versión y rollback). El CRUD directo de dominios, glosario y
    naming, `propagate` y `rephysicalize` responden 409 antes de cualquier
    efecto — ni siquiera la auditoría —; sin permiso sigue siendo 403. Lecturas,
    cómputos y lock/unlock siguen."""
    admin, ana, diego = api("admin"), api("ana"), api("diego")
    pid = world["pid"]
    admin.post(f"/api/projects/{pid}/standards/apply", {
        "kind": "batch", "termsUpsert": [{"term": "Sucursal", "abbrev": "SUC", "scope": "column"}],
        "domainsUpsert": [{"name": "Fecha", "defaultDataType": "DATE"}]})
    (dom,) = ana.get(f"/api/projects/{pid}/domains")
    (term,) = ana.get(f"/api/projects/{pid}/glossary")
    closed = [
        ("POST", f"/api/projects/{pid}/domains", {"name": "Otro", "defaultDataType": "DATE"}),
        ("PUT", f"/api/projects/{pid}/domains/{dom['id']}", {"name": "Fecha", "defaultDataType": "TIMESTAMP"}),
        ("DELETE", f"/api/projects/{pid}/domains/{dom['id']}", None),
        ("POST", f"/api/projects/{pid}/domains/{dom['id']}/propagate", None),
        ("POST", f"/api/projects/{pid}/glossary", {"term": "Cuenta", "abbrev": "CTA", "scope": "column"}),
        ("PUT", f"/api/projects/{pid}/glossary/{term['id']}", {"term": "Sucursal", "abbrev": "SUCU", "scope": "column"}),
        ("DELETE", f"/api/projects/{pid}/glossary/{term['id']}", None),
        ("POST", f"/api/projects/{pid}/glossary/rephysicalize", {"scope": "column"}),
        ("PUT", f"/api/projects/{pid}/settings/naming/column", {"separator": "_", "case": "upper", "maxLength": 60}),
        ("PUT", f"/api/projects/{pid}/settings/naming/nope", {"separator": "_", "case": "upper"}),
    ]
    before = _whole_db(fake_db.raw)
    bad = []
    for method, path, body in closed:
        st, data = admin.call(method, path, body)
        if st != 409 or not isinstance(data, dict) or data.get("detail") != STANDARDS_VERSIONED:
            bad.append(f"admin {method} {path} -> {st} {data}")
        st, data = diego.call(method, path, body)
        if st != 403:
            bad.append(f"lector {method} {path} -> {st} {data}")
    assert not bad, "\n".join(bad)
    assert _whole_db(fake_db.raw) == before

    assert ana.get(f"/api/projects/{pid}/domains/{dom['id']}/impact") is not None
    assert ana.get(f"/api/projects/{pid}/domains/{dom['id']}/impact/columns") is not None
    admin.call("POST", f"/api/projects/{pid}/glossary/{term['id']}/lock", expect=200)
    admin.call("POST", f"/api/projects/{pid}/glossary/{term['id']}/unlock", expect=200)
    st, phys = admin.call("POST", f"/api/projects/{pid}/glossary/physicalize", {"logical": "Sucursal Cliente"}, expect=200)
    assert phys["physical"].startswith("SUC")
    ana.call("POST", f"/api/projects/{pid}/glossary/validate", {"term": "Cuenta", "scope": "column"}, expect=200)
    ana.call("POST", f"/api/projects/{pid}/glossary/impact", {"scope": "column", "termsUpsert": []}, expect=200)
    assert ana.get(f"/api/projects/{pid}/settings/naming")["column"]["case"]

    st, prof = admin.call("POST", f"/api/projects/{pid}/upload-profiles/default", expect=201)
    profiles = ana.get(f"/api/projects/{pid}/upload-profiles")
    assert len(profiles) == 1 and profiles[0]["projectId"] == pid
    assert ana.get(f"/api/projects/{pid}/upload-profiles/{profiles[0]['id']}")["id"] == profiles[0]["id"]
    admin.call("POST", f"/api/projects/{pid}/upload-profiles/{profiles[0]['id']}/default", expect=200)
    assert ana.get(f"/api/projects/{pid}/upload-profiles/catalog")

    rule = {"name": "Comentario", "condition": "", "action": {}}
    st, out = admin.call("POST", f"/api/projects/{pid}/ddl-rules/test", {"rule": rule, "tableId": world["t1"]})
    assert st < 500
    assert ana.get(f"/api/projects/{pid}/ddl-rules/templates")


def test_admin_reporting_and_changeset_schema_ops(api, world):
    admin, ana = api("admin"), api("ana")
    pid = world["pid"]

    admin.call("POST", "/api/admin/users", {"username": "nuevo@local", "role": "lector"}, expect=400)   # email inválido
    admin.call("POST", "/api/admin/users", {"username": "local_sin_clave", "role": "lector"}, expect=400)   # local sin contraseña
    st, user = admin.call("POST", "/api/admin/users", {"username": "nuevo@empresa.com", "role": "lector"}, expect=201)
    admin.call("PUT", "/api/admin/users/nuevo@empresa.com", {"status": "disabled"}, expect=200)
    api("nuevo@empresa.com").call("POST", "/api/folders", {"projectId": pid, "name": "x"}, expect=403)  # deshabilitado
    admin.call("DELETE", "/api/admin/users/nuevo@empresa.com", expect=200)
    admin.call("DELETE", "/api/admin/users/nuevo@empresa.com", expect=404)
    admin.call("PUT", "/api/admin/roles/auditor", {"name": "Auditor", "permissions": {"model.view": True}}, expect=200)
    assert "auditor" in {r["id"] for r in admin.get("/api/admin/roles")}
    admin.call("DELETE", "/api/admin/roles/auditor", expect=200)
    assert admin.get("/api/admin/audit?limit=5")
    ana.call("PUT", "/api/admin/roles/x", {"name": "x"}, expect=403)             # sin admin.manage

    rows = admin.post("/api/reporting/query", {"projectId": pid, "from": "columns", "select": ["physicalName"], "limit": 10})
    assert rows is not None
    st, _ = admin.call("POST", "/api/reporting/query/sql", {"sql": "SELECT physicalName FROM columns", "projectId": pid})
    assert st < 500

    # Rename/delete de esquema DENTRO de un draft (server-side, con propagación).
    cs = ana.post("/api/changesets/snapshot", {"projectId": pid}, expect=201)["id"]
    ana.call("POST", f"/api/changesets/{cs}/schemas/{world['schema']}/delete", expect=409)   # en uso
    out = ana.post(f"/api/changesets/{cs}/schemas/{world['schema']}/rename", {"newName": "STG_NEW"})
    assert out == {"tables": 2, "views": 0}
    eff = ana.get(f"/api/changesets/{cs}/effective/canonical_tables")
    assert {t["schema"] for t in eff} == {"STG_NEW"}
    ana.call("POST", f"/api/changesets/{cs}/schemas/{world['schema']}/rename", {"newName": ""}, expect=422)

    # Rutas legadas / inexistentes no revientan.
    st, _ = ana.call("POST", "/api/changesets", {"title": "legacy"})
    assert st in (404, 405)
    ana.call("DELETE", f"/api/changesets/{cs}/uploads/no-such-job", expect=404)
    ana.change(cs, "canonical_tables", "t-x", table_payload("M_X", "X"))
    assert ana.get(f"/api/changesets/{cs}")["status"] == "draft"
