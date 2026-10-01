"""Doc 105 (D1): el modelo se escribe SIEMPRE por una versión en edición. Las
escrituras directas a producción (sin changeset) que el backend todavía
aceptaba con sólo `model.edit` — sin revisión, sin unicidad, cruzando
proyectos, dejando canvases imposibles de editar y sin auditoría — responden
409 y no tocan nada. El alta de un proyecto sigue directa (doc 75 D5)."""
from __future__ import annotations

import copy

MSG = "This change requires a version in edit mode."
VERSIONED = ("folders", "subject_areas", "schemas", "canonical_tables", "canonical_columns",
             "relationships", "views", "projects")


def _snapshot(raw):
    return {c: sorted((copy.deepcopy(d) for d in raw[c].find({})), key=lambda d: d["_id"]) for c in VERSIONED}


def _calls(w):
    pid, t1, c_a = w["pid"], w["t1"], w["c_a"]
    rel = {"parentTableId": t1, "childTableId": w["t2"], "pairs": [{"parentColumnId": c_a, "childColumnId": w["c_c"]}]}
    view = {"name": "V_X", "schema": "STG_VU", "sourceTableIds": [t1], "sources": [{"column": "CODCLIENTE", "tableId": t1}]}
    return [
        ("POST", "/api/subject-areas", {"projectId": pid, "name": "C"}),
        ("PUT", f"/api/subject-areas/{w['canvas']}", {"projectId": pid, "name": "C2"}),
        ("PUT", f"/api/subject-areas/{w['canvas']}/tables", {"tableIds": [t1]}),
        ("PUT", f"/api/subject-areas/{w['canvas']}/views", {"viewIds": []}),
        ("PUT", f"/api/subject-areas/{w['canvas']}/layout", {"layout": {t1: {"x": 1, "y": 1}}}),
        ("PUT", f"/api/subject-areas/{w['canvas']}/drawings", {"drawings": []}),
        ("DELETE", f"/api/subject-areas/{w['canvas']}", None),
        ("POST", "/api/folders", {"projectId": pid, "name": "F"}),
        ("PATCH", f"/api/folders/{w['folder']}", {"name": "F2"}),
        ("DELETE", f"/api/folders/{w['folder']}", None),
        ("POST", f"/api/projects/{pid}/schemas", {"name": "ODS"}),
        ("PATCH", f"/api/schemas/{w['schema']}", {"name": "STG2"}),
        ("DELETE", f"/api/schemas/{w['schema']}", None),
        ("POST", "/api/views", view),
        ("PUT", f"/api/views/{w['view']}", view),
        ("DELETE", f"/api/views/{w['view']}", None),
        ("POST", "/api/relationships", rel),
        ("PUT", f"/api/relationships/{w['rel']}", rel),
        ("DELETE", f"/api/relationships/{w['rel']}", None),
        ("POST", f"/api/projects/{pid}/catalog/tables", {"logicalName": "T", "physicalName": "M_T", "schema": "STG"}),
        ("POST", f"/api/catalog/tables/{t1}/columns", {"logicalName": "C", "physicalName": "C", "dataType": "STRING"}),
    ]


def test_toda_escritura_directa_del_modelo_es_409_y_no_toca_nada(api, world, fake_db):
    ana = api("ana")
    before = _snapshot(fake_db.raw)
    bad = []
    for method, path, body in _calls(world):
        st, data = ana.call(method, path, body)
        detail = data.get("detail") if isinstance(data, dict) else data
        if st != 409 or detail != MSG:
            bad.append(f"{method} {path} -> {st} {data}")
    assert not bad, "\n".join(bad)
    assert _snapshot(fake_db.raw) == before


def test_lo_legitimo_sigue(api, world):
    admin, ana, diego = api("admin"), api("ana"), api("diego")
    admin.post("/api/projects", {"name": "Otro"}, expect=201)                     # doc 75 D5: alta directa
    assert ana.get(f"/api/subject-areas/{world['canvas']}")["name"]               # lecturas
    assert ana.get(f"/api/projects/{world['pid']}/schemas")
    diego.call("PUT", f"/api/subject-areas/{world['canvas']}/layout", {"layout": {}}, expect=403)  # sin permiso: 403
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]      # el camino versionado
    ana.change(cs, "folders", "f-nueva", {"projectId": world["pid"], "name": "Nueva"})
