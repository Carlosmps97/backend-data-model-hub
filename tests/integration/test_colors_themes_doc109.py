"""Doc 109 — colores por HTTP, de punta a punta (router → service → repos
reales sobre la BD en memoria):

- Themes del proyecto en Data Standards: alta/edición/baja versionadas, nombres
  únicos, color válido, rollback y lectura para el canvas.
- Color de la TABLA (todos sus canvases) y excepciones del CANVAS: viajan por
  el changeset, se ven en el draft, se publican, la escritura del cliente es
  estricta y la lectura tolerante.
"""
from __future__ import annotations

from tests.integration.conftest import publish


def _apply(admin, pid, body, expect=200):
    return admin.call("POST", f"/api/projects/{pid}/standards/apply", body, expect=expect)[1]


def _themes(u, pid):
    return u.get(f"/api/projects/{pid}/themes")


def _draft(api, w, user="ana"):
    return api(user).post("/api/changesets/snapshot", {"projectId": w["pid"]}, expect=201)["id"]


def _canvas(w, **over):
    return {"projectId": w["pid"], "folderId": w["folder"], "name": "Modelo Clientes",
            "tableIds": [w["t1"], w["t2"]], "viewIds": [w["view"]],
            "layout": {w["t1"]: {"x": 0, "y": 0}, w["t2"]: {"x": 400, "y": 0}}, **over}


# ── Themes en Data Standards ────────────────────────────────────────────────


def test_themes_se_crean_editan_y_borran_versionados(api, world):
    admin, pid = api("admin"), world["pid"]
    v = _apply(admin, pid, {"kind": "themes", "themesUpsert": [
        {"name": "Entidad Principal", "color": "#ffff80"},
        {"name": "Entidad Secundaria", "color": "#80A3FF"}]})
    assert v["kind"] == "themes"
    assert v["diff"]["added"] == ["Theme Entidad Principal · #FFFF80", "Theme Entidad Secundaria · #80A3FF"]
    themes = _themes(api("ana"), pid)                     # lectura abierta (el canvas la usa)
    assert [(t["name"], t["color"], t["order"]) for t in themes] == [
        ("Entidad Principal", "#FFFF80", 0), ("Entidad Secundaria", "#80A3FF", 1)]
    snap = admin.get(f"/api/projects/{pid}/standards/snapshot")
    assert [t["name"] for t in snap["themes"]] == ["Entidad Principal", "Entidad Secundaria"]

    pri = themes[0]
    v2 = _apply(admin, pid, {"kind": "themes", "themesUpsert": [{**pri, "color": "#FFC000"}]})
    assert v2["diff"]["edited"] == ["Theme Entidad Principal · #FFFF80 → #FFC000"]
    # una edición idéntica no es un cambio
    assert _apply(admin, pid, {"kind": "themes", "themesUpsert": [{**pri, "color": "#ffc000"}]},
                  expect=422)["detail"] == "There are no changes to apply."

    v3 = _apply(admin, pid, {"kind": "themes", "themesDelete": [pri["id"]]})
    assert v3["diff"]["removed"] == ["Theme Entidad Principal"]
    assert [t["name"] for t in _themes(admin, pid)] == ["Entidad Secundaria"]

    # rollback a la versión con el color original: el theme vuelve, con su color
    admin.post(f"/api/projects/{pid}/standards/rollback", {"targetSeq": v["seq"]})
    back = {t["name"]: t["color"] for t in _themes(admin, pid)}
    assert back == {"Entidad Principal": "#FFFF80", "Entidad Secundaria": "#80A3FF"}


def test_themes_validan_nombre_unico_y_color(api, world):
    admin, pid = api("admin"), world["pid"]
    _apply(admin, pid, {"kind": "themes", "themesUpsert": [{"name": "Referencia", "color": "#979797"}]})
    dup = _apply(admin, pid, {"kind": "themes", "themesUpsert": [{"name": " referencia ", "color": "#000000"}]},
                 expect=422)
    assert "already a theme named 'referencia'" in dup["detail"]
    bad = _apply(admin, pid, {"kind": "themes", "themesUpsert": [{"name": "Gris", "color": "gris"}]}, expect=422)
    assert "is not a color" in bad["detail"]
    blank = _apply(admin, pid, {"kind": "themes", "themesUpsert": [{"name": "  ", "color": "#000000"}]}, expect=422)
    assert blank["detail"] == "Every theme needs a name."
    # renombrar uno y crear otro con el nombre viejo en el MISMO lote vale
    (t,) = _themes(admin, pid)
    _apply(admin, pid, {"kind": "themes", "themesUpsert": [{**t, "name": "Ref"},
                                                            {"name": "Referencia", "color": "#C0C0C0"}]})
    assert sorted(x["name"] for x in _themes(admin, pid)) == ["Ref", "Referencia"]


def test_themes_de_un_proyecto_no_se_ven_en_otro(api, world):
    from tests.integration.conftest import build_world
    admin, pa = api("admin"), world["pid"]
    pb = build_world(api, "Proyecto B", prefix="b")["pid"]
    _apply(admin, pa, {"kind": "themes", "themesUpsert": [{"name": "Solo A", "color": "#111111"}]})
    assert _themes(admin, pb) == []
    (t,) = _themes(admin, pa)
    out = _apply(admin, pb, {"kind": "themes", "themesUpsert": [{**t, "color": "#222222"}]}, expect=409)
    assert "belongs to another project" in out["detail"]


# ── Color de tabla y excepciones del canvas (changeset) ─────────────────────


def test_color_de_tabla_y_del_canvas_viajan_por_el_changeset(api, world):
    ana, w = api("ana"), world
    cs = _draft(api, w)
    table = ana.get(f"/api/changesets/{cs}/effective/canonical_tables?ids={w['t1']}")[0]
    assert table.get("color") is None
    ana.change(cs, "canonical_tables", w["t1"], {**table, "color": "theme:t-pri"})
    ana.change(cs, "subject_areas", w["canvas"], _canvas(w, colors={w["t2"]: "#92d050", w["t1"]: "none"}))

    dia = ana.get(f"/api/subject-areas/{w['canvas']}/diagram?changesetId={cs}")
    t1 = next(t for t in dia["tables"] if t["id"] == w["t1"])
    assert t1["color"] == "theme:t-pri"
    assert dia["subjectArea"]["colors"] == {w["t2"]: "#92D050", w["t1"]: "none"}
    # producción intacta hasta aprobar
    prod = ana.get(f"/api/subject-areas/{w['canvas']}/diagram")
    assert prod["subjectArea"]["colors"] == {}
    assert next(t for t in prod["tables"] if t["id"] == w["t1"]).get("color") is None

    publish(api, cs)
    prod = ana.get(f"/api/subject-areas/{w['canvas']}/diagram")
    assert prod["subjectArea"]["colors"] == {w["t2"]: "#92D050", w["t1"]: "none"}
    assert next(t for t in prod["tables"] if t["id"] == w["t1"])["color"] == "theme:t-pri"


def test_la_escritura_del_cliente_es_estricta(api, world):
    ana, w = api("ana"), world
    cs = _draft(api, w)
    table = ana.get(f"/api/changesets/{cs}/effective/canonical_tables?ids={w['t1']}")[0]
    st, out = ana.change(cs, "canonical_tables", w["t1"], {**table, "color": "verde"}, expect=422)
    assert "is not a color" in str(out)
    st, out = ana.change(cs, "subject_areas", w["canvas"], _canvas(w, colors={w["t1"]: "rojo"}), expect=422)
    assert "colors." in str(out)
    st, out = ana.change(cs, "subject_areas", w["canvas"], _canvas(w, colors=["no", "mapa"]), expect=422)
    assert "colors must be an object" in str(out)


def test_rollback_quita_el_color_agregado_despues(api, world):
    """El publish es un $set: la imagen previa de la tabla (sin `color`) debe
    quitar el color que la versión agregó."""
    ana, w = api("ana"), world
    cs = _draft(api, w)
    table = ana.get(f"/api/changesets/{cs}/effective/canonical_tables?ids={w['t1']}")[0]
    ana.change(cs, "canonical_tables", w["t1"], {**table, "color": "#FF0000"})
    ana.change(cs, "subject_areas", w["canvas"], _canvas(w, colors={w["t2"]: "#00FF00"}))
    publish(api, cs)
    # volver a la versión ANTERIOR (la del mundo base, sin colores)
    rb = ana.post(f"/api/changesets/{w['v2']['id']}/rollback", {})
    publish(api, rb["id"])
    prod = ana.get(f"/api/subject-areas/{w['canvas']}/diagram")
    assert next(t for t in prod["tables"] if t["id"] == w["t1"]).get("color") is None
    assert prod["subjectArea"]["colors"] == {}


def test_themes_normalizan_espacios_y_rechazan_ids_invalidos(api, world):
    admin, pid = api("admin"), world["pid"]
    bad = _apply(admin, pid, {"kind": "themes", "themesUpsert": [
        {"id": "Entidad Principal", "name": "X", "color": "#000000"}]}, expect=422)
    assert "not a valid theme id" in bad["detail"]          # no cabría en `theme:<id>`
    _apply(admin, pid, {"kind": "themes", "themesUpsert": [{"name": "  Gris  ", "color": " #c0c0c0 "}]})
    (t,) = _themes(admin, pid)
    assert (t["name"], t["color"]) == ("Gris", "#C0C0C0")
    same = _apply(admin, pid, {"kind": "themes", "themesUpsert": [{**t, "color": " #c0c0c0"}]}, expect=422)
    assert same["detail"] == "There are no changes to apply."


def test_un_color_corrupto_en_la_bd_no_bloquea_la_tabla(api, world, fake_db):
    """Lectura tolerante del color de tabla (como los del canvas): sale como sin
    color y la tabla se sigue pudiendo editar (antes: 422 en cada guardado)."""
    ana, w = api("ana"), world
    fake_db.raw["canonical_tables"].update_one({"_id": w["t1"]}, {"$set": {"color": "theme:{ABC}+0001"}})
    cs = _draft(api, w)
    table = ana.get(f"/api/changesets/{cs}/effective/canonical_tables?ids={w['t1']}")[0]
    assert table["color"] is None
    ana.change(cs, "canonical_tables", w["t1"], {**table, "description": "otra"})
    dia = ana.get(f"/api/subject-areas/{w['canvas']}/diagram?changesetId={cs}")
    assert next(t for t in dia["tables"] if t["id"] == w["t1"])["color"] is None
