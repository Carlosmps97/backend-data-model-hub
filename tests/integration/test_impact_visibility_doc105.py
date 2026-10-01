"""Doc 105 (H8): `/relationships/impact` con un `changesetId` lee el overlay
del draft — debe respetar la misma visibilidad que `/links` (doc 70 §12): el
dueño, sus revisores y los administradores sí; otro modelador o un lector no."""
from __future__ import annotations

from tests.integration.conftest import column_payload, table_payload


def test_impact_respeta_la_visibilidad_del_draft(api, world):
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    path = f"/api/relationships/impact?columnId={world['c_a']}&changesetId={cs}"
    for user in ("carla", "diego"):
        api(user).get(path, expect=403)
    assert ana.get(path) is not None                       # la dueña
    assert api("admin").get(path) is not None              # administrador
    assert api("carla").get(f"/api/relationships/impact?columnId={world['c_a']}") is not None   # producción


def test_impact_de_una_version_eliminada_sigue_siendo_404(api, world):
    api("carla").get(f"/api/relationships/impact?columnId={world['c_a']}&changesetId=no-such-cs", expect=404)


def test_h8_impact_respeta_la_visibilidad_sin_romper_a_los_legitimos(api, world):
    ana = api("ana")
    cs_id = api("ana").post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    ana.change(cs_id, "canonical_tables", "t-sec", table_payload("M_SECRETA", "Secreta"))
    ana.change(cs_id, "canonical_columns", "c-sec", column_payload("t-sec", "CODCLIENTE", "Cod", 0, isForeignKey=True))
    ana.change(cs_id, "relationships", "r-sec", {
        "parentTableId": world["t1"], "childTableId": "t-sec",
        "pairs": [{"parentColumnId": world["c_a"], "childColumnId": "c-sec"}]})
    url = f"/api/relationships/impact?columnId={world['c_a']}&changesetId={cs_id}"
    for intruso in ("diego", "carla"):
        st, _ = api(intruso).call("GET", url)
        assert st == 403
    assert ana.get(url)["total"] == 2                     # dueño (sesión de edición)
    assert api("admin").get(url)["total"] == 2            # administrador (view_all)
    ana.post(f"/api/changesets/{cs_id}/submit", {"reviewers": ["beto"]})
    assert api("beto").get(url)["total"] == 2             # revisor asignado
    assert api("diego").get(f"/api/relationships/impact?columnId={world['c_a']}&changesetId=asof:{world['v1']}") is not None
    assert api("diego").get(f"/api/relationships/impact?columnId={world['c_a']}")["total"] == 1   # producción
