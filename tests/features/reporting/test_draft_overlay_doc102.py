"""Doc 102: la versión propia superpuesta a lo publicado para el Reporting —
misma semántica que el canvas y el Explorer (upsert reemplaza, delete quita,
altas del proyecto entran) y re-filtro del alcance pedido. Puro."""
from __future__ import annotations

from app.features.reporting.draft import (
    changes_of, overlay_columns, overlay_named, overlay_project, overlay_views,
)

P = "p1"


def up(**payload):
    return {"op": "upsert", "payload": {"projectId": P, **payload}}


DEL = {"op": "delete"}


def test_changes_of():
    assert changes_of(None, "views") == {} and changes_of({"views": {"v": DEL}}, "views") == {"v": DEL}
    assert changes_of({"views": {}}, "canonical_tables") == {}


def test_overlay_project_renombra_quita_agrega_y_revive_solo_del_proyecto():
    pub = [{"id": "t1", "physicalName": "A"}, {"id": "t2", "physicalName": "B"}]
    changes = {"t2": up(physicalName="B2"), "t1": DEL, "t9": DEL,
               "t4": up(physicalName="NUEVA"),                      # alta (o revive) del proyecto
               "t5": {"op": "upsert", "payload": {"projectId": "p9", "physicalName": "AJENA"}}}
    out = {d["id"]: d["physicalName"] for d in overlay_project(pub, changes, P)}
    assert out == {"t2": "B2", "t4": "NUEVA"}


def test_overlay_project_sin_cambios_devuelve_lo_publicado():
    pub = [{"id": "t1"}]
    assert overlay_project(pub, {}, P) is pub


def test_overlay_columns_respeta_el_lote():
    pub = [{"id": "c1", "tableId": "t1"}, {"id": "c5", "tableId": "t1"}]
    changes = {"c1": DEL, "c2": up(tableId="t1"), "c3": up(tableId="t2"),
               "c5": up(tableId="t2")}                              # la versión la mudó fuera del lote
    assert [c["id"] for c in overlay_columns(pub, changes, P, {"t1"})] == ["c2"]
    assert sorted(c["id"] for c in overlay_columns(pub, changes, P, set())) == ["c2", "c3", "c5"]


def test_overlay_views_por_fuentes_y_por_esquema():
    pub = [{"id": "v1", "sourceTableIds": ["t1"], "schema": "a_vu"}]
    changes = {"v1": up(sourceTableIds=["t2"], schema="a_vu"),     # re-apuntada fuera del lote
               "v2": up(sourceTableIds=["t1"], schema="a_vu"),
               "v3": up(sourceTableIds=["t9"], schema="b_vu"),
               "v4": up(tableId="t1", schema="a_vu")}              # legacy: solo tableId
    assert sorted(v["id"] for v in overlay_views(pub, changes, P, {"t1"})) == ["v2", "v4"]
    assert sorted(v["id"] for v in overlay_views(pub, changes, P, set(), "a_vu")) == ["v1", "v2", "v4"]
    assert sorted(v["id"] for v in overlay_views(pub, changes, P, set())) == ["v1", "v2", "v3", "v4"]


def test_overlay_named():
    docs = {"t1": {"physicalName": "A"}, "t2": {"physicalName": "B"}}
    changes = {"t1": up(physicalName="A2"), "t2": DEL, "t3": up(physicalName="C"), "t8": up(physicalName="X")}
    out = overlay_named(docs, changes, ["t1", "t2", "t3"])
    assert {k: v["physicalName"] for k, v in out.items()} == {"t1": "A2", "t3": "C"}
    assert docs == {"t1": {"physicalName": "A"}, "t2": {"physicalName": "B"}}    # no muta la entrada
