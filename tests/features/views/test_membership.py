"""Doc 70 §2.2: regla PURA de membresía de vistas en el canvas (legacy vs
lista explícita) — única definición compartida por diagrama, listado por
tabla, reporting y backfill."""
from __future__ import annotations

from app.features.views.membership import filter_canvas_views, view_on_canvas, view_sources

V1 = {"id": "v1", "sourceTableIds": ["t1"], "showOnCanvas": True}
V2 = {"id": "v2", "sourceTableIds": ["t2"], "showOnCanvas": True}
V3 = {"id": "v3", "tableId": "t1", "showOnCanvas": False}        # legacy sin sourceTableIds


def test_view_sources_canonico_y_legacy():
    assert view_sources(V1) == ["t1"]
    assert view_sources(V3) == ["t1"]
    assert view_sources({}) == []


def test_legacy_none_aplica_regla_vieja():
    assert [v["id"] for v in filter_canvas_views([V1, V2, V3], None, ["t1"])] == ["v1"]


def test_lista_explicita_ignora_show_on_canvas_y_exige_fuente_presente():
    # v3 entra aunque showOnCanvas=False (es miembro); v2 no tiene fuente en el canvas.
    assert [v["id"] for v in filter_canvas_views([V1, V2, V3], ["v3", "v2"], ["t1"])] == ["v3"]
    # lista vacía = explícitamente sin vistas, aunque haya candidatas legacy.
    assert filter_canvas_views([V1, V2], [], ["t1", "t2"]) == []


def test_conserva_el_orden_de_entrada():
    out = filter_canvas_views([V2, V1], ["v1", "v2"], ["t1", "t2"])
    assert [v["id"] for v in out] == ["v2", "v1"]


def test_view_on_canvas():
    assert view_on_canvas(V1, {"tableIds": ["t1"], "viewIds": None})            # legacy
    assert not view_on_canvas(V3, {"tableIds": ["t1"], "viewIds": None})        # legacy sin flag
    assert view_on_canvas(V3, {"tableIds": ["t1"], "viewIds": ["v3"]})          # explícito
    assert not view_on_canvas(V1, {"tableIds": ["t1"], "viewIds": []})
    assert not view_on_canvas(V1, {"tableIds": [], "viewIds": ["v1"]})          # sin fuente presente
