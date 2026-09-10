"""Escrituras DIRECTAS (fuera del changeset) por HTTP (doc 82): los endpoints
que el front usa en modo publicado o para estructura/estándares. Cada uno
atraviesa router → service → repositorio reales con el proyecto derivado
server-side (doc 75 I1). Un 5xx acá es un bug de cableado."""
from __future__ import annotations

from tests.integration.conftest import table_payload


def test_structure_endpoints(api, world):
    ana = api("ana")
    pid, t1 = world["pid"], world["t1"]

    # Esquemas: crear / renombrar / borrar (sin uso).
    st, sch = ana.call("POST", f"/api/projects/{pid}/schemas", {"name": "ODS", "kind": "tables"}, expect=201)
    assert sch["projectId"] == pid
    ana.call("POST", f"/api/projects/{pid}/schemas", {"name": "ods"}, expect=409)              # único por proyecto (CI)
    ana.call("PATCH", f"/api/schemas/{sch['id']}", {"name": "ODS_2"}, expect=200)
    assert {s["name"] for s in ana.get(f"/api/projects/{pid}/schemas")} == {"STG", "ODS_2"}
    ana.call("DELETE", f"/api/schemas/{world['schema']}", expect=409)                            # en uso
    ana.call("DELETE", f"/api/schemas/{sch['id']}", expect=200)

    # Carpetas: crear / renombrar / mover / borrar.
    st, fld = ana.call("POST", "/api/folders", {"projectId": pid, "name": "Nueva"}, expect=201)
    ana.call("PATCH", f"/api/folders/{fld['id']}", {"name": "Renombrada", "parentFolderId": world["folder"]}, expect=200)
    got = ana.get(f"/api/folders/{fld['id']}")
    assert got["name"] == "Renombrada" and got["parentFolderId"] == world["folder"] and got["projectId"] == pid
    assert len(ana.get(f"/api/folders?projectId={pid}")) == 2
    ana.call("DELETE", f"/api/folders/{fld['id']}", expect=200)
    ana.get(f"/api/folders/{fld['id']}", expect=404)

    # Canvases: crear / editar / membresía / layout / drawings / diagrama / borrar.
    st, sa = ana.call("POST", "/api/subject-areas", {"projectId": pid, "name": "Canvas directo"}, expect=201)
    sid = sa["id"]
    ana.put(f"/api/subject-areas/{sid}", {"projectId": pid, "name": "Canvas directo 2", "folderId": world["folder"]})
    ana.put(f"/api/subject-areas/{sid}/tables", {"tableIds": [t1]})
    ana.put(f"/api/subject-areas/{sid}/views", {"viewIds": [world["view"]]})
    ana.put(f"/api/subject-areas/{sid}/layout", {"layout": {t1: {"x": 10, "y": 20}}})
    ana.put(f"/api/subject-areas/{sid}/drawings", {"drawings": [{"type": "text", "text": "hola"}]})
    diagram = ana.get(f"/api/subject-areas/{sid}/diagram")
    assert [t["id"] for t in diagram["tables"]] == [t1] and diagram["subjectArea"]["layout"][t1] == {"x": 10, "y": 20}
    assert len(ana.get(f"/api/projects/{pid}/subject-areas")) == 2
    ana.call("DELETE", f"/api/subject-areas/{sid}", expect=200)
    ana.get(f"/api/subject-areas/{sid}", expect=404)
    ana.call("DELETE", f"/api/subject-areas/{sid}", expect=404)


def test_catalog_views_and_relationships_endpoints(api, world):
    ana = api("ana")
    pid, t1, c_a = world["pid"], world["t1"], world["c_a"]

    st, tbl = ana.call("POST", f"/api/projects/{pid}/catalog/tables",
                       {"logicalName": "Tabla Directa", "physicalName": "M_DIRECTA", "schema": "STG"}, expect=201)
    assert tbl["projectId"] == pid
    st, col = ana.call("POST", f"/api/catalog/tables/{tbl['id']}/columns",
                       {"logicalName": "Codigo Cliente", "physicalName": "CODCLIENTE", "dataType": "STRING"}, expect=201)
    assert col["projectId"] == pid and col["tableId"] == tbl["id"]
    assert [c["physicalName"] for c in ana.get(f"/api/catalog/tables/{tbl['id']}/columns")] == ["CODCLIENTE"]
    assert {t["physicalName"] for t in ana.get(f"/api/projects/{pid}/catalog/tables?q=DIRECTA")} == {"M_DIRECTA"}

    rel_body = {"parentTableId": t1, "childTableId": tbl["id"],
                "pairs": [{"parentColumnId": c_a, "childColumnId": col["id"]}]}
    st, rel = ana.call("POST", "/api/relationships", rel_body, expect=201)
    assert rel["projectId"] == pid
    ana.call("PUT", f"/api/relationships/{rel['id']}", {**rel_body, "identifying": True}, expect=200)
    assert ana.get(f"/api/relationships/links?tableId={tbl['id']}") is not None
    assert ana.get(f"/api/relationships?tableId={tbl['id']}")
    assert ana.get(f"/api/relationships/impact?columnId={col['id']}") is not None
    ana.call("DELETE", f"/api/relationships/{rel['id']}", expect=200)

    st, view = ana.call("POST", "/api/views", {"name": "V_DIRECTA", "schema": "STG_VU", "sourceTableIds": [t1],
                                              "sources": [{"column": "CODCLIENTE", "tableId": t1}]}, expect=201)
    assert view["projectId"] == pid
    ana.call("PUT", f"/api/views/{view['id']}", {"name": "V_DIRECTA_2", "schema": "STG_VU", "sourceTableIds": [t1],
                                                 "sources": [{"column": "CODCLIENTE", "tableId": t1}]}, expect=200)
    assert {v["name"] for v in ana.get(f"/api/views?projectId={pid}")} >= {"V_CLIENTE", "V_DIRECTA_2"}
    assert ana.get(f"/api/views/for-table?tableId={t1}")
    ana.call("POST", "/api/views", {"name": "SIN_FUENTE"}, expect=409)
    ana.call("DELETE", f"/api/views/{view['id']}", expect=200)
    assert ana.get(f"/api/projects/{pid}/catalog/views/{world['view']}/columns") is not None


def test_standards_direct_endpoints(api, world):
    admin, ana = api("admin"), api("ana")
    pid = world["pid"]
    st, dom = admin.call("POST", f"/api/projects/{pid}/domains", {"name": "Fecha", "defaultDataType": "DATE"}, expect=201)
    assert dom["projectId"] == pid
    admin.call("PUT", f"/api/projects/{pid}/domains/{dom['id']}", {"name": "Fecha", "defaultDataType": "TIMESTAMP"}, expect=200)
    assert ana.get(f"/api/projects/{pid}/domains/{dom['id']}/impact") is not None
    admin.call("POST", f"/api/projects/{pid}/domains/{dom['id']}/propagate", expect=200)
    admin.call("DELETE", f"/api/projects/{pid}/domains/{dom['id']}", expect=200)

    st, term = admin.call("POST", f"/api/projects/{pid}/glossary", {"term": "Sucursal", "abbrev": "SUC", "scope": "column"}, expect=201)
    assert term["projectId"] == pid
    admin.call("PUT", f"/api/projects/{pid}/glossary/{term['id']}", {"term": "Sucursal", "abbrev": "SUCU", "scope": "column"}, expect=200)
    admin.call("POST", f"/api/projects/{pid}/glossary/{term['id']}/lock", expect=200)
    admin.call("POST", f"/api/projects/{pid}/glossary/{term['id']}/unlock", expect=200)
    st, phys = admin.call("POST", f"/api/projects/{pid}/glossary/physicalize", {"logical": "Sucursal Cliente"}, expect=200)
    assert phys["physical"].startswith("SUCU")
    admin.call("POST", f"/api/projects/{pid}/glossary/rephysicalize", {"scope": "column"}, expect=200)
    admin.call("DELETE", f"/api/projects/{pid}/glossary/{term['id']}", expect=200)

    admin.put(f"/api/projects/{pid}/settings/naming/column", {"separator": "_", "case": "upper", "maxLength": 60})
    assert ana.get(f"/api/projects/{pid}/settings/naming")["column"]["maxLength"] == 60
    admin.call("PUT", f"/api/projects/{pid}/settings/naming/nope", {"separator": "_", "case": "upper"}, expect=400)

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
