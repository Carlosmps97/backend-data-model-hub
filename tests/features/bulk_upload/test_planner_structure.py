"""Planner — estructura (doc 55 §4.1 / §6): espacios (carpeta raíz), subjects
(subcarpeta), canvases y esquemas se crean o reusan por nombre CI, y las tablas
entran a su canvas con una posición provisional. Doc 75: el proyecto es el del
changeset (el workbook no crea proyectos; `project-mismatch` si nombra otro). Puro."""
from __future__ import annotations

from app.features.bulk_upload.context import UploadContext
from app.features.bulk_upload.plan_structure import StructurePlanner
from app.features.bulk_upload.plan_tables import TablePlan
from app.features.bulk_upload.planner import build_plan
from app.features.bulk_upload.report import ReportBuilder

from .helpers import by_coll, codes, ctx, parsed, seq_ids, trow


def test_crea_space_subject_canvas_esquema_y_tabla_en_orden_dentro_del_proyecto():
    p = parsed(tables=[trow(3, "Cliente", project="P", space="S", subject="SA", diagram="D", schema="ddv")])
    plan = build_plan(p, ctx(), new_id=seq_ids())
    assert plan.has_errors is False and plan.report["warnings"] == []
    colls = [c["collection"] for c in plan.changes]
    assert colls == ["folders", "folders", "subject_areas", "schemas", "canonical_tables"]
    b = by_coll(plan)
    assert "projects" not in b                       # doc 75: el proyecto es el del changeset
    space, subject = b["folders"]
    canvas, = b["subject_areas"]
    schema, = b["schemas"]
    table, = b["canonical_tables"]
    assert (space["payload"]["name"], space["payload"]["parentFolderId"], space["payload"]["projectId"]) == ("S", None, "p1")
    assert (subject["payload"]["name"], subject["payload"]["parentFolderId"]) == ("SA", space["entityId"])
    assert (canvas["payload"]["name"], canvas["payload"]["projectId"], canvas["payload"]["folderId"]) == ("D", "p1", subject["entityId"])
    assert canvas["payload"]["tableIds"] == [table["entityId"]]
    assert set(canvas["payload"]["layout"]) == {table["entityId"]}
    assert schema["payload"] == {"id": schema["entityId"], "projectId": "p1", "name": "ddv", "description": None, "kind": "tables"}
    assert table["payload"]["schema"] == "ddv" and table["payload"]["physicalName"] == "CLIENTE"
    assert table["payload"]["projectId"] == "p1"
    assert plan.affected_canvas_ids == [canvas["entityId"]]
    s = plan.report["summary"]
    assert (s["projects"]["create"], s["folders"]["create"], s["canvases"]["create"], s["schemas"]["create"], s["tables"]["create"]) == (0, 2, 1, 1, 1)


def test_project_mismatch_es_error_y_no_crea_proyectos():
    c = UploadContext(project_id="p1", project_name="DDV")
    rb = ReportBuilder()
    sp = StructurePlanner(c, rb, lambda: "id")
    tp = TablePlan(row=3, logical="x", physical="X", schema="s", id="t1",
                   canvas={"project": "UDV", "space": None, "subject": None, "diagram": "D"})
    sp.place(tp)
    assert any(i.code == "project-mismatch" for i in rb._errors)
    assert sp.changes() == []


def test_reusa_proyecto_carpeta_y_canvas_existentes_por_nombre_ci():
    c = ctx(project_name="Proyecto X",
            folders=[{"id": "f1", "projectId": "p1", "parentFolderId": None, "name": "Space 1", "order": 0}],
            canvases=[{"id": "sa1", "projectId": "p1", "folderId": "f1", "name": "Diag", "tableIds": [],
                       "layout": {}, "drawings": [], "udpValues": {}}],
            schemas=[{"id": "s1", "name": "ddv", "kind": "tables"}])
    p = parsed(tables=[trow(3, "Cliente", project="PROYECTO X", space="space 1", diagram="diag", schema="DDV")])
    plan = build_plan(p, c, new_id=seq_ids())
    b = by_coll(plan)
    assert "projects" not in b and "folders" not in b and "schemas" not in b
    canvas, = b["subject_areas"]
    assert canvas["entityId"] == "sa1" and canvas["op"] == "upsert"
    assert canvas["payload"]["tableIds"] == [b["canonical_tables"][0]["entityId"]]
    assert canvas["payload"]["name"] == "Diag"           # doc completo, nombre intacto
    assert b["canonical_tables"][0]["payload"]["schema"] == "ddv"   # grafía de la plataforma
    assert codes(plan, "warning") == ["existing-canvas"]
    assert plan.report["summary"]["canvases"] == {"create": 0, "update": 1, "unchanged": 0}
    assert plan.affected_canvas_ids == ["sa1"]


def test_tabla_existente_ya_en_el_canvas_no_genera_cambios():
    c = ctx(canvases=[{"id": "sa1", "projectId": "p1", "folderId": None, "name": "D", "tableIds": ["t1"],
                       "layout": {"t1": {"x": 1, "y": 2}}, "drawings": [], "udpValues": {}}],
            schemas=[{"id": "s1", "name": "ddv", "kind": "tables"}],
            tables=[{"id": "t1", "projectId": "p1", "physicalName": "CLIENTE", "logicalName": "Cliente", "schema": "ddv",
                     "description": None, "udpValues": {}}])
    plan = build_plan(parsed(tables=[trow(3, "Cliente", project="P", diagram="D", schema="ddv")]), c)
    assert plan.changes == [] and plan.affected_canvas_ids == []
    assert plan.report["summary"]["tables"] == {"create": 0, "update": 0, "unchanged": 1}
    assert plan.report["summary"]["canvases"] == {"create": 0, "update": 0, "unchanged": 1}
    assert plan.report["warnings"] == []


def test_diagrama_sin_project_usa_el_proyecto_de_la_version():
    """Doc 78: la plantilla nueva no trae PROJECT — la estructura cuelga del
    proyecto del changeset (doc 75: nunca se crea otro)."""
    plan = build_plan(parsed(tables=[trow(3, "Cliente", subject="SA", diagram="D", schema="ddv")]), ctx(), new_id=seq_ids())
    assert plan.has_errors is False
    b = by_coll(plan)
    folder, = b["folders"]
    canvas, = b["subject_areas"]
    assert (folder["payload"]["projectId"], folder["payload"]["parentFolderId"]) == ("p1", None)
    assert canvas["payload"]["projectId"] == "p1" and canvas["payload"]["folderId"] == folder["entityId"]
    assert plan.report["tables"][0]["canvas"] == "SA / D"


def test_tabla_nueva_sin_diagrama_es_warning_no_canvas():
    plan = build_plan(parsed(tables=[trow(3, "Cliente", schema="ddv")]), ctx())
    assert plan.has_errors is False
    assert codes(plan, "warning") == ["no-canvas"]
    assert plan.affected_canvas_ids == []


def test_subject_sin_space_cuelga_de_la_raiz_del_proyecto():
    plan = build_plan(parsed(tables=[trow(3, "Cliente", project="P", subject="SA", diagram="D", schema="ddv")]), ctx())
    folder, = by_coll(plan)["folders"]
    assert (folder["payload"]["name"], folder["payload"]["parentFolderId"]) == ("SA", None)


def test_dos_filas_al_mismo_canvas_nuevo_crean_uno_solo_con_ambas_tablas():
    p = parsed(tables=[trow(3, "Cliente", project="P", diagram="D", schema="ddv"),
                       trow(4, "Cuenta", project="p", diagram="d", schema="ddv")])
    plan = build_plan(p, ctx(), new_id=seq_ids())
    b = by_coll(plan)
    assert "projects" not in b and len(b["subject_areas"]) == 1 and len(b["schemas"]) == 1
    canvas, = b["subject_areas"]
    assert canvas["payload"]["tableIds"] == [t["entityId"] for t in b["canonical_tables"]]
    assert len(canvas["payload"]["layout"]) == 2


def test_carpeta_nueva_toma_el_orden_siguiente_entre_hermanas():
    c = ctx(folders=[{"id": "f1", "projectId": "p1", "parentFolderId": None, "name": "A", "order": 0},
                     {"id": "f2", "projectId": "p1", "parentFolderId": None, "name": "B", "order": 1}])
    plan = build_plan(parsed(tables=[trow(3, "Cliente", project="P", space="C", schema="ddv")]), c)
    folder, = by_coll(plan)["folders"]
    assert folder["payload"]["order"] == 2


def test_layout_provisional_debajo_del_extremo_actual_del_canvas():
    c = ctx(canvases=[{"id": "sa1", "projectId": "p1", "folderId": None, "name": "D", "tableIds": ["t0"],
                       "layout": {"t0": {"x": 40, "y": 1000}}, "drawings": [], "udpValues": {}}],
            tables=[{"id": "t0", "projectId": "p1", "physicalName": "OTRA", "logicalName": "Otra", "schema": "ddv"}],
            schemas=[{"id": "s1", "name": "ddv", "kind": "tables"}])
    plan = build_plan(parsed(tables=[trow(3, "Cliente", project="P", diagram="D", schema="ddv")]), c, new_id=seq_ids())
    canvas, = by_coll(plan)["subject_areas"]
    tid = by_coll(plan)["canonical_tables"][0]["entityId"]
    assert canvas["payload"]["tableIds"] == ["t0", tid]
    assert canvas["payload"]["layout"]["t0"] == {"x": 40, "y": 1000}
    assert canvas["payload"]["layout"][tid]["y"] >= 1000 + 360
