"""Doc 105 — Reporting por HTTP sobre la app REAL (routers, RBAC, versiones):

- R2-A1: `GET /columns?tableIds=,` (y `/views?tableIds=`) — un lote que queda
  vacío tras el split era «todo el proyecto».
- R2-A4: hueco de seguridad SIN test — quitar `open_version` de `/columns`
  dejaba la suite verde (expondría drafts ajenos y versiones cerradas)."""
from __future__ import annotations


def _draft(api, w: dict, user: str = "ana") -> str:
    return api(user).post("/api/changesets/snapshot", {"projectId": w["pid"]}, expect=201)["id"]


# ── R2-A1 ───────────────────────────────────────────────────────────────────
def test_r2a1_get_con_tableids_presente_pero_vacio_es_422(api, world):
    ana, pid = api("ana"), world["pid"]
    for path in ("/api/reporting/columns", "/api/reporting/views"):
        for q in ("tableIds=,", "tableIds=", "tableIds=,,,"):
            status, body = ana.call("GET", f"{path}?projectId={pid}&{q}")
            assert status == 422, (path, q, status)
            assert body["detail"] == "At least one table id is required."
        assert ana.get(f"{path}?projectId={pid}")                  # sin `tableIds`: «sin lote» (con tope)
        assert ana.get(f"{path}?projectId={pid}&tableIds={world['t1']}")


# ── R2-A4 ───────────────────────────────────────────────────────────────────
def test_r2a4_columns_rechaza_version_cerrada_y_ajena(api, world):
    """Sin `open_version` en `/columns`, ana leería el draft de carla (y una
    versión ya publicada como si siguiera abierta)."""
    ana, pid, t1 = api("ana"), world["pid"], world["t1"]
    ajena = _draft(api, world, "carla")
    for cs, code in ((world["v2"]["id"], 409), (ajena, 403)):
        ana.get(f"/api/reporting/columns?projectId={pid}&tableId={t1}&changesetId={cs}", expect=code)
        ana.post("/api/reporting/columns/query", {"projectId": pid, "tableIds": [t1], "changesetId": cs},
                 expect=code)
    propia = _draft(api, world, "ana")                                    # la propia sí abre
    assert ana.post("/api/reporting/columns/query", {"projectId": pid, "tableIds": [t1], "changesetId": propia})


# ── A3-o1: POST /insights/relationships/query ──────────────────────────────
REL_LOT = "/api/reporting/insights/relationships/query"


def _ends(rows: list[dict]) -> list[tuple]:
    return [(r["id"], r["pairIndex"], r["parent"], r["child"]) for r in rows]


def test_a3o1_lote_en_produccion_son_las_mismas_filas_del_get(api, world):
    ana, pid = api("ana"), world["pid"]
    full = ana.get(f"/api/reporting/insights/relationships?projectId={pid}")
    for lot in ([world["t1"]], [world["t2"]], [world["t1"], world["t2"]]):
        rows = ana.post(REL_LOT, {"projectId": pid, "tableIds": lot})
        assert rows == [r for r in full if r["parentTableId"] in lot or r["childTableId"] in lot]
    assert _ends(rows) == [(world["rel"], 0, "M_CLIENTE.CODCLIENTE", "M_CUENTA.CODCLIENTE")]
    assert ana.post(REL_LOT, {"projectId": pid, "tableIds": ["no-existe"]}) == []


def test_a3o1_lote_con_la_version_propia(api, world):
    ana, pid, t1, t2 = api("ana"), world["pid"], world["t1"], world["t2"]
    cs = _draft(api, world)
    ana.change(cs, "relationships", world["rel"], None, op="delete")
    ana.change(cs, "relationships", "rel-nueva", {
        "parentTableId": t1, "childTableId": t2, "identifying": True,
        "pairs": [{"parentColumnId": world["c_a"], "childColumnId": world["c_c"]}]})
    rows = ana.post(REL_LOT, {"projectId": pid, "tableIds": [t2], "changesetId": cs})
    assert [(r["id"], r["identifying"]) for r in rows] == [("rel-nueva", True)]
    assert _ends(rows) == [("rel-nueva", 0, "M_CLIENTE.CODCLIENTE", "M_CUENTA.CODCLIENTE")]
    assert [r["id"] for r in ana.post(REL_LOT, {"projectId": pid, "tableIds": [t2]})] == [world["rel"]]


def test_a3o1_lote_vacio_o_sobre_el_tope_es_422(api, world):
    ana, pid = api("ana"), world["pid"]
    for ids in ([], ["", ""], [f"t{i}" for i in range(301)]):
        ana.post(REL_LOT, {"projectId": pid, "tableIds": ids}, expect=422)
    assert ana.post(REL_LOT, {"projectId": pid, "tableIds": [f"t{i}" for i in range(300)]}) == []


def test_a3o1_version_ajena_cerrada_o_inexistente(api, world):
    ana, pid, t1 = api("ana"), world["pid"], world["t1"]
    ajena = _draft(api, world, "carla")
    for cs, code in ((ajena, 403), (world["v2"]["id"], 409), ("no-existe", 404)):
        ana.post(REL_LOT, {"projectId": pid, "tableIds": [t1], "changesetId": cs}, expect=code)


# ── A3: vistas con `tableIds` Y `schema` en una versión ─────────────────────
def test_a3_views_con_tableids_y_schema_en_version_respeta_los_dos(api, world):
    ana, pid, t1 = api("ana"), world["pid"], world["t1"]
    cs = _draft(api, world)
    ana.change(cs, "views", "v-otra", {"name": "V_OTRA", "schema": "OTRO_VU", "sourceTableIds": [t1],
                                       "sources": [{"column": "CODCLIENTE", "tableId": t1}]})
    q = f"/api/reporting/views?projectId={pid}&tableIds={t1}&changesetId={cs}"
    assert [v["name"] for v in ana.get(f"{q}&schema=STG_VU")] == ["V_CLIENTE"]
    assert [v["name"] for v in ana.get(f"{q}&schema=OTRO_VU")] == ["V_OTRA"]
