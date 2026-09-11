"""Doc 87 §3.6 — invariante: la carga es UPSERT, nunca destructiva. Todo cambio
es `upsert`; tablas, columnas, vistas, canvases y carpetas que el workbook no
menciona no aparecen en el plan (quedan intactos)."""
from __future__ import annotations

from app.features.bulk_upload.planner import build_plan

from .helpers import by_coll, crow, ctx, parsed, trow

SCHEMAS = [{"id": "s1", "name": "ddv", "kind": "tables"}, {"id": "s2", "name": "ddv_vu", "kind": "views"}]


def _table(tid, physical, logical):
    return {"id": tid, "projectId": "p1", "physicalName": physical, "logicalName": logical, "schema": "ddv",
            "description": None, "udpValues": {}}


def _col(cid, table, physical, ordinal):
    return {"id": cid, "projectId": "p1", "tableId": table, "physicalName": physical, "logicalName": physical,
            "parentDomainId": None, "dataType": "STRING", "typeOverridden": False, "isPrimaryKey": None,
            "pkPosition": None, "isForeignKey": None, "isNullable": True, "isPartition": False,
            "description": None, "ordinal": ordinal, "udpValues": {}}


def _ctx():
    return ctx(
        schemas=SCHEMAS,
        folders=[{"id": "f1", "projectId": "p1", "parentFolderId": None, "name": "Otra carpeta", "order": 0}],
        canvases=[{"id": "sa1", "projectId": "p1", "folderId": None, "name": "D", "tableIds": ["t1", "t9"],
                   "viewIds": ["v1", "v9"], "layout": {"t1": {"x": 1, "y": 1}, "t9": {"x": 2, "y": 2}},
                   "drawings": [], "udpValues": {}}],
        tables=[_table("t1", "CLIENTE", "Cliente"), _table("t9", "AJENA", "Ajena")],
        views=[{"id": "v1", "name": "CLIENTE", "schema": "ddv_vu", "sourceTableIds": ["t1"]},
               {"id": "v9", "name": "AJENA", "schema": "ddv_vu", "sourceTableIds": ["t9"]}],
        columns_by_table={"t1": [_col("c1", "t1", "COD", 0), _col("c2", "t1", "OTRA", 1)]},
    )


def test_la_carga_nunca_emite_deletes_ni_toca_lo_no_mencionado():
    p = parsed(tables=[trow(3, "Cliente", diagram="D", description="Definición nueva")],
               columns=[crow(3, "Cliente", "COD", physical="COD", data_type="STRING")])
    plan = build_plan(p, _ctx())
    assert plan.has_errors is False
    assert all(ch["op"] == "upsert" for ch in plan.changes)
    # Solo la tabla mencionada cambia (su descripción); su otra columna, la
    # tabla/vista ajenas, la vista propia, el canvas y la carpeta quedan fuera.
    assert [(ch["collection"], ch["entityId"]) for ch in plan.changes] == [("canonical_tables", "t1")]
    assert plan.affected_canvas_ids == []
    s = plan.report["summary"]
    assert (s["tables"], s["columns"], s["views"], s["canvases"]) == (
        {"create": 0, "update": 1, "unchanged": 0}, {"create": 0, "update": 0, "unchanged": 1},
        {"create": 0, "update": 0, "unchanged": 1}, {"create": 0, "update": 0, "unchanged": 1})


def test_re_cargar_lo_mismo_no_produce_cambios():
    p = parsed(tables=[trow(3, "Cliente", diagram="D")],
               columns=[crow(3, "Cliente", "COD", physical="COD", data_type="STRING"),
                        crow(4, "Cliente", "OTRA", physical="OTRA", data_type="STRING")])
    plan = build_plan(p, _ctx())
    assert plan.changes == [] and plan.has_errors is False and plan.report["warnings"] == []
    assert "views" not in by_coll(plan)
