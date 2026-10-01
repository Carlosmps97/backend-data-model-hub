"""Doc 105 (A2-o3) · C9 de `audit_data_consistency`: además de `tableIds` y del
layout, poda los `viewIds` colgando (vista borrada o inexistente) — un canvas
con una vista colgando no se podía editar. `viewIds` None (canvas legacy: las
vistas salen por `showOnCanvas`) no se toca. Idempotente.

Revisión R2: los activos iban en conjuntos GLOBALES — un miembro vivo de OTRO
proyecto pasaba como sano, pero la app rechaza todo guardado de ese canvas
(409 «… belongs to another project»). Ahora los activos van por proyecto
(`{id: projectId}`) y el miembro ajeno se poda como los muertos (C9d)."""
from __future__ import annotations

from scripts import audit_data_consistency as audit
from tests.integration.conftest import api, build_world, fake_db, world  # noqa: F401  (fixtures)

ZERO = {"tableIds": 0, "viewIds": 0, "layout": 0, "otherProject": 0}


class _SyncColl:
    """Colección mongomock con `bulk_write` despachado op a op (el de mongomock
    no acepta los `UpdateOne` de pymongo ≥ 4.10; ver `tests/support/fakedb.py`)."""

    def __init__(self, coll) -> None:
        self._c = coll

    def __getattr__(self, name: str):
        return getattr(self._c, name)

    def bulk_write(self, ops: list, ordered: bool = True) -> None:
        for op in ops:
            self._c.update_one(op._filter, op._doc, upsert=bool(op._upsert))


class _SyncDb:
    """El handle sync que usa el script (`get_sync_db()`), sobre `fake_db.raw`."""

    def __init__(self, raw) -> None:
        self._raw = raw

    def __getitem__(self, name: str) -> _SyncColl:
        return _SyncColl(self._raw[name])

    def __getattr__(self, name: str) -> _SyncColl:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


def test_c9_poda_miembros_muertos_y_respeta_el_canvas_legacy():
    canvases = [
        {"_id": "sa1", "projectId": "p1", "tableIds": ["t1"], "viewIds": ["v1", "v-muerta"],
         "layout": {"t1": {"x": 0}, "v1": {"x": 1}, "v-muerta": {"x": 2}}},
        {"_id": "sa2", "projectId": "p1", "tableIds": ["t1", "t-muerta"], "viewIds": None, "layout": {}},  # legacy
        {"_id": "sa3", "projectId": "p1", "tableIds": ["t1"], "viewIds": ["v1"], "layout": {"t1": {"x": 0}}},
        {"_id": "sa4", "projectId": "p1"},                                                   # sin miembros
    ]
    updates, counts = audit.canvas_member_fixes(canvases, {"t1": "p1"}, {"v1": "p1"})
    assert dict(updates) == {
        "sa1": {"viewIds": ["v1"], "layout": {"t1": {"x": 0}, "v1": {"x": 1}}},
        "sa2": {"tableIds": ["t1"]},
    }
    assert counts == {"tableIds": 1, "viewIds": 1, "layout": 1, "otherProject": 0}

    fixed = [{**c, **dict(updates).get(c["_id"], {})} for c in canvases]
    assert audit.canvas_member_fixes(fixed, {"t1": "p1"}, {"v1": "p1"}) == ([], ZERO)


def test_c9_poda_los_miembros_vivos_de_otro_proyecto():
    active_t = {"t1": "p1", "t-ajena": "p2"}
    active_v = {"v1": "p1", "v-ajena": "p2"}
    canvases = [
        {"_id": "sa1", "projectId": "p1", "tableIds": ["t1", "t-ajena"], "viewIds": ["v1", "v-ajena"],
         "layout": {"t1": {"x": 0}, "t-ajena": {"x": 1}, "v-ajena": {"x": 2}}},
        {"_id": "sa2", "projectId": "p1", "tableIds": ["t1"], "viewIds": ["v-ajena"], "layout": {}},
        {"_id": "sa3", "projectId": "p2", "tableIds": ["t-ajena"], "viewIds": ["v-ajena"], "layout": {}},  # sano
        {"_id": "sa4", "projectId": "p1", "tableIds": ["t-ajena"], "viewIds": None, "layout": {}},  # legacy
    ]
    updates, counts = audit.canvas_member_fixes(canvases, active_t, active_v)
    assert dict(updates) == {
        "sa1": {"tableIds": ["t1"], "viewIds": ["v1"], "layout": {"t1": {"x": 0}}},
        "sa2": {"viewIds": []},
        "sa4": {"tableIds": []},
    }
    assert counts == {"tableIds": 0, "viewIds": 0, "layout": 1, "otherProject": 3}
    fixed = [{**c, **dict(updates).get(c["_id"], {})} for c in canvases]
    assert audit.canvas_member_fixes(fixed, active_t, active_v) == ([], ZERO)


def test_c9_sin_projectId_no_juzga_el_proyecto():
    """Un canvas o un miembro SIN `projectId` (dato anterior al doc 75) no se
    poda por proyecto — sólo por actividad —: no se sabe de quién es y podar
    por falta de dato vaciaría canvases enteros."""
    canvases = [{"_id": "sa1", "tableIds": ["t1", "t-p2"], "viewIds": ["v-sin"], "layout": {}},
                {"_id": "sa2", "projectId": "p1", "tableIds": ["t-sin"], "viewIds": [], "layout": {}}]
    updates, counts = audit.canvas_member_fixes(
        canvases, {"t1": "p1", "t-p2": "p2", "t-sin": None}, {"v-sin": None})
    assert (updates, counts) == ([], ZERO)


def test_c9_ve_la_vista_de_otro_proyecto_y_el_fix_destraba_el_canvas(api, world, fake_db):
    """Repro del revisor (R2): una vista VIVA de OTRO proyecto en el canvas (stock
    de las escrituras directas, D1) pasaba C9 como sana, pero la app rechazaba
    cada guardado del canvas (409 cross_project). Con el fix de C9 aplicado, el
    canvas se vuelve a poder guardar."""
    wb = build_world(api, "Proyecto B", prefix="b")
    fake_db.raw["subject_areas"].update_one({"_id": world["canvas"]}, {"$push": {"viewIds": wb["view"]}})

    def _guardar_canvas(user: str) -> tuple[int, object]:
        u = api(user)
        cs = u.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
        sa = fake_db.raw["subject_areas"].find_one({"_id": world["canvas"]})
        payload = {k: v for k, v in sa.items() if k not in ("_id", "flgactive", "createdAt", "updatedAt")}
        payload["name"] = "Modelo Clientes (renombrado)"
        return u.call("PUT", f"/api/changesets/{cs}/changes", {
            "collection": "subject_areas", "entityId": world["canvas"], "op": "upsert", "payload": payload})

    status, body = _guardar_canvas("ana")
    assert status == 409 and "belongs to another project" in str(body), (status, body)   # el síntoma
    canvases = list(fake_db.raw["subject_areas"].find({}, {"_id": 1, "projectId": 1, "tableIds": 1,
                                                           "viewIds": 1, "layout": 1}))
    active_t = {t["_id"]: t.get("projectId") for t in fake_db.raw["canonical_tables"].find(audit.ACTIVE)}
    active_v = {v["_id"]: v.get("projectId") for v in fake_db.raw["views"].find(audit.ACTIVE)}
    updates, counts = audit.canvas_member_fixes(canvases, active_t, active_v)
    assert counts["otherProject"] == 1, counts
    assert dict(updates) == {world["canvas"]: {"viewIds": [world["view"]]}}
    for sid, upd in updates:                                                   # lo que hace --fix
        fake_db.raw["subject_areas"].update_one({"_id": sid}, {"$set": upd})
    status, body = _guardar_canvas("carla")
    assert status == 200, body


def test_c9d_main_reporta_y_con_fix_poda_el_miembro_ajeno(api, world, fake_db, monkeypatch):
    """El script completo (`main`) sobre la BD: sin `--fix` sólo reporta C9d;
    con `--fix` poda la tabla y la vista ajenas del canvas; la re-corrida queda
    en 0 (idempotente). Cubre la proyección de `projectId` del canvas."""
    wb = build_world(api, "Proyecto B", prefix="b")
    fake_db.raw["subject_areas"].update_one({"_id": world["canvas"]},
                                            {"$push": {"viewIds": wb["view"], "tableIds": wb["t1"]}})
    monkeypatch.setattr(audit, "db", _SyncDb(fake_db.raw))
    code = "C9d canvases con tablas/vistas vivas de OTRO proyecto (409 al guardar)"

    def _run(fix: bool) -> dict[str, int]:
        monkeypatch.setattr(audit, "FIX", fix)
        monkeypatch.setattr(audit, "ISSUES", {})
        audit.main()
        return dict(audit.ISSUES)

    assert _run(False)[code] == 1
    sa = fake_db.raw["subject_areas"].find_one({"_id": world["canvas"]})
    assert wb["view"] in sa["viewIds"] and wb["t1"] in sa["tableIds"]              # el reporte no toca
    assert _run(True)[code] == 1
    sa = fake_db.raw["subject_areas"].find_one({"_id": world["canvas"]})
    assert sa["tableIds"] == [world["t1"], world["t2"]] and sa["viewIds"] == [world["view"]]
    assert _run(False)[code] == 0
    other = fake_db.raw["subject_areas"].find_one({"_id": wb["canvas"]})             # el de B, intacto
    assert other["tableIds"] == [wb["t1"], wb["t2"]] and other["viewIds"] == [wb["view"]]


# ── Revisión R5: símbolos de subcategoría y canvases inactivos ─────────────
# El canvas guarda también la posición de los SÍMBOLOS de subcategoría
# (`layout[subtypeSymbolId]`, doc 53; el front persiste nodos table/view/subcat):
# C9b los contaba como «nodos inexistentes» y `--fix` los borraba. Y C9 recorría
# también los canvases borrados.

def test_c9_conserva_la_posicion_de_los_simbolos_de_subcategoria_vivos_del_proyecto():
    sym = "0b6f4f7e-5a3c-4d0e-9d1b-3c2f1a0e9b77"                         # relationships.subtypeSymbolId
    canvases = [
        {"_id": "sa1", "projectId": "p1", "tableIds": ["t-party", "t-ind"], "viewIds": [],
         "layout": {"t-party": {"x": 0, "y": 0}, "t-ind": {"x": 0, "y": 300}, sym: {"x": 40, "y": 150}}},
        {"_id": "sa2", "projectId": "p1", "tableIds": ["t-party"], "viewIds": [],
         "layout": {"t-party": {"x": 0, "y": 0}, sym: {"x": 1, "y": 1},
                    "sym-de-relacion-borrada": {"x": 2, "y": 2}, "sym-ajeno": {"x": 3, "y": 3}}},
    ]
    active_t = {"t-party": "p1", "t-ind": "p1"}
    updates, counts = audit.canvas_member_fixes(canvases, active_t, {}, {sym: "p1", "sym-ajeno": "p2"})
    assert dict(updates) == {"sa2": {"layout": {"t-party": {"x": 0, "y": 0}, sym: {"x": 1, "y": 1}}}}
    assert counts["layout"] == 1


def test_c9_main_conserva_los_simbolos_y_solo_mira_canvases_activos(api, world, fake_db, monkeypatch):
    raw = fake_db.raw
    raw["relationships"].insert_one({"_id": "rel-sub", "projectId": world["pid"], "flgactive": True,
                                     "parentTableId": world["t1"], "childTableId": world["t2"],
                                     "pairs": [{"parentColumnId": world["c_a"], "childColumnId": world["c_c"]}],
                                     "subcategory": True, "subtypeSymbolId": "sym-1"})
    raw["relationships"].insert_one({"_id": "rel-sub-muerta", "projectId": world["pid"], "flgactive": False,
                                     "subtypeSymbolId": "sym-muerto"})
    wb = build_world(api, "Proyecto B", prefix="b")
    raw["relationships"].insert_one({"_id": "rel-sub-b", "projectId": wb["pid"], "flgactive": True,
                                     "parentTableId": wb["t1"], "childTableId": wb["t2"],
                                     "pairs": [{"parentColumnId": wb["c_a"], "childColumnId": wb["c_c"]}],
                                     "subcategory": True, "subtypeSymbolId": "sym-b"})
    raw["subject_areas"].update_one({"_id": world["canvas"]}, {"$set": {
        "layout.sym-1": {"x": 7, "y": 8}, "layout.sym-muerto": {"x": 1, "y": 1}, "layout.sym-b": {"x": 2, "y": 2}}})
    raw["subject_areas"].insert_one({"_id": "sa-borrado", "projectId": world["pid"], "flgactive": False,
                                     "tableIds": ["t-que-no-existe"], "viewIds": ["v-que-no-existe"],
                                     "layout": {"basura": {"x": 0, "y": 0}}})
    monkeypatch.setattr(audit, "db", _SyncDb(raw))

    def _run(fix: bool) -> dict[str, int]:
        monkeypatch.setattr(audit, "FIX", fix)
        monkeypatch.setattr(audit, "ISSUES", {})
        audit.main()
        return {k: v for k, v in audit.ISSUES.items() if k.startswith("C9")}

    assert _run(False)["C9b canvases con layout de nodos inexistentes o de otro proyecto"] == 1   # el de A
    _run(True)
    layout = raw["subject_areas"].find_one({"_id": world["canvas"]})["layout"]
    assert layout["sym-1"] == {"x": 7, "y": 8}                                  # símbolo vivo del proyecto
    assert "sym-muerto" not in layout and "sym-b" not in layout                 # de relación borrada / ajeno
    assert set(_run(False).values()) == {0}
    assert raw["subject_areas"].find_one({"_id": "sa-borrado"})["tableIds"] == ["t-que-no-existe"]
