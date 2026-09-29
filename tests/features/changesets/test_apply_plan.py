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


def test_doc_models_sin_estandares_y_projects_sigue_versionado():
    from app.features.changesets.repository import VERSIONED
    from app.features.changesets.validation import DOC_MODELS
    assert "parent_domains" not in DOC_MODELS and "glossary_terms" not in DOC_MODELS
    assert "projects" in DOC_MODELS and VERSIONED[0] == "projects"


def test_project_change_error_solo_el_propio_proyecto():
    from app.features.changesets.validation import project_change_error
    assert project_change_error("p1", "p1", "upsert", {"name": "DDV"}) is None
    assert project_change_error("p1", "p1", "delete", None) is None
    assert "another project" in project_change_error("p1", "p2", "delete", None)
    assert "another project" in project_change_error("p1", "p2", "upsert", {"name": "x"})
    assert "cannot change" in project_change_error("p1", "p1", "upsert", {"name": "x", "id": "p9"})


def test_apply_plan_fija_las_llaves_de_frase_de_una_relacion():
    """Doc 98: el apply es un $set (merge) — una llave AUSENTE no borra la
    publicada. La imagen previa de una relación anterior al doc 98 no trae las
    frases: el plan las lleva en None para que un rollback SÍ las quite."""
    before_image = {"projectId": "p1", "parentTableId": "t1", "childTableId": "t2",
                    "pairs": [{"parentColumnId": "c1", "childColumnId": "c2"}]}
    changes = {"relationships": {
        "r-legacy": {"op": "upsert", "payload": before_image},
        "r-frase": {"op": "upsert", "payload": {**before_image, "childToParentPhrase": "es de un Cliente"}},
        "r-del": {"op": "delete"},
    }}
    plan = {eid: payload for _c, eid, _op, payload in apply_plan(changes)}
    assert plan["r-legacy"]["parentToChildPhrase"] is None
    assert plan["r-legacy"]["childToParentPhrase"] is None
    assert plan["r-legacy"]["pairs"] == before_image["pairs"]          # lo demás, intacto
    assert plan["r-frase"]["childToParentPhrase"] == "es de un Cliente"   # la que viene, gana
    assert plan["r-frase"]["parentToChildPhrase"] is None
    assert plan["r-del"] is None
    assert "parentToChildPhrase" not in before_image                  # puro: no muta la entrada


def test_apply_plan_no_agrega_frases_a_otras_colecciones():
    changes = {"canonical_tables": {"t1": {"op": "upsert", "payload": {"physicalName": "M_X"}}}}
    assert apply_plan(changes)[0][3] == {"physicalName": "M_X"}



# ── Doc 99: trazos manuales de wires (`routes` del canvas) ──────────────────

_CANVAS = {"projectId": "p1", "name": "Canvas", "tableIds": ["t1"],
           "layout": {"t1": {"x": 0, "y": 0}}, "drawings": [{"id": "d1"}]}


def test_apply_plan_fija_routes_en_un_canvas_que_no_lo_trae():
    """El apply es un $set (merge): una llave AUSENTE no pisa la publicada. Un
    canvas sin `routes` (draft de un cliente viejo, o la imagen previa de un
    canvas anterior al doc 99) publicaría SUS posiciones con los trazos de OTRO,
    y un rollback no podría quitar un trazo agregado después."""
    changes = {"subject_areas": {"sa1": {"op": "upsert", "payload": dict(_CANVAS)}}}
    payload = apply_plan(changes)[0][3]
    assert payload["routes"] == {}
    assert {k: v for k, v in payload.items() if k != "routes"} == _CANVAS    # lo demás, intacto
    assert "routes" not in _CANVAS                                          # puro: no muta la entrada


def test_apply_plan_conserva_el_trazo_que_trae_el_canvas():
    routes = {"rel-1": [{"x": 420, "y": 180}, {"x": 420, "y": 96}], "subsym-s1": [{"x": 1, "y": 2}]}
    changes = {"subject_areas": {"sa1": {"op": "upsert", "payload": {**_CANVAS, "routes": routes}}}}
    assert apply_plan(changes)[0][3]["routes"] == routes


def test_apply_plan_sanea_los_trazos_antes_de_publicar():
    """Lo que arma el backend (cascada de membresía, rollback) parte del
    documento publicado tal cual: un trazo corrupto no llega a producción."""
    changes = {"subject_areas": {
        "sa-nulo": {"op": "upsert", "payload": {**_CANVAS, "routes": None}},
        "sa-roto": {"op": "upsert", "payload": {**_CANVAS, "routes": {
            "ok": [{"x": 1, "y": 2, "z": 9}], "roto": [{"x": "a", "y": 2}], "vacio": []}}},
        "sa-raro": {"op": "upsert", "payload": {**_CANVAS, "routes": ["no", "es", "mapa"]}},
    }}
    plan = {eid: payload for _c, eid, _op, payload in apply_plan(changes)}
    assert plan["sa-nulo"]["routes"] == {}
    assert plan["sa-roto"]["routes"] == {"ok": [{"x": 1, "y": 2}]}
    assert plan["sa-raro"]["routes"] == {}


def test_apply_plan_no_toca_un_canvas_borrado_ni_otras_colecciones():
    changes = {
        "subject_areas": {"sa-del": {"op": "delete"}},
        "folders": {"f1": {"op": "upsert", "payload": {"projectId": "p1", "name": "Carpeta"}}},
    }
    plan = {eid: payload for _c, eid, _op, payload in apply_plan(changes)}
    assert plan["sa-del"] is None
    assert plan["f1"] == {"projectId": "p1", "name": "Carpeta"}


def test_apply_plan_no_lleva_llaves_reservadas_al_set():
    """Doc 100 (P1/P2): un cambio grabado ANTES del arreglo (o armado por el
    backend desde un documento viejo) con `a.b`, `$x` o `_id` publica igual,
    sin esas llaves: el `$set` las leería como camino, operador o identidad."""
    changes = {
        "subject_areas": {"sa1": {"op": "upsert", "payload": {
            "name": "C", "layout": {"t1": {"x": 1, "y": 2}},
            "layout.evil": {"x": "a"}, "$foo": 1, "_id": "otro"}}},
        "canonical_tables": {"t1": {"op": "upsert", "payload": {"physicalName": "T", "udpValues.x": "v"}}},
        "canonical_columns": {"c1": {"op": "delete"}},
    }
    plan = {(c, e): (op, p) for c, e, op, p in apply_plan(changes)}
    assert plan[("subject_areas", "sa1")] == ("upsert", {"name": "C", "layout": {"t1": {"x": 1, "y": 2}}, "routes": {}})
    assert plan[("canonical_tables", "t1")] == ("upsert", {"physicalName": "T"})
    assert plan[("canonical_columns", "c1")] == ("delete", None)
