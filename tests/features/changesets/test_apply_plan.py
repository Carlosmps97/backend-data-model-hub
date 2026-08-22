"""`apply_plan` linealiza los `changes` en operaciones (collection, id, op, payload)."""
from __future__ import annotations

from app.features.changesets.service import apply_plan


def test_apply_plan_linealiza():
    changes = {
        "parent_domains": {"pd-1": {"op": "upsert", "payload": {"name": "monto"}}},
        "canonical_tables": {"ct-1": {"op": "delete"}},
    }
    plan = apply_plan(changes)
    assert ("parent_domains", "pd-1", "upsert", {"name": "monto"}) in plan
    assert ("canonical_tables", "ct-1", "delete", None) in plan
    assert len(plan) == 2


def test_apply_plan_normaliza_payloads_de_views():
    """I4 (final review): `views` ∈ VERSIONED, así que un upsert de vista vía
    changeset se publicaba CRUDO — sin la normalización tableId↔sourceTableIds
    de F3a. Un doc showOnCanvas=true con solo `tableId` legacy quedaba invisible
    en todo canvas (build_canvas_query matchea SOLO sourceTableIds). apply_plan
    normaliza el payload antes de materializarlo."""
    changes = {
        "views": {
            "v-legacy": {"op": "upsert", "payload": {
                "name": "vw_legacy", "tableId": "t1", "showOnCanvas": True}},
            "v-multi": {"op": "upsert", "payload": {
                "name": "vw_multi", "sourceTableIds": ["t2", "t3"]}},
            "v-del": {"op": "delete"},
        },
        # Las demás colecciones NO se tocan.
        "canonical_tables": {"ct-1": {"op": "upsert", "payload": {"physicalName": "TBL"}}},
    }
    plan = {(c, e): (op, p) for c, e, op, p in apply_plan(changes)}
    _, legacy = plan[("views", "v-legacy")]
    assert legacy["sourceTableIds"] == ["t1"]      # legacy tableId → canónico
    assert legacy["showOnCanvas"] is True
    _, multi = plan[("views", "v-multi")]
    assert multi["tableId"] == "t2"                # compat: primera fuente
    assert plan[("views", "v-del")] == ("delete", None)
    assert plan[("canonical_tables", "ct-1")] == ("upsert", {"physicalName": "TBL"})
