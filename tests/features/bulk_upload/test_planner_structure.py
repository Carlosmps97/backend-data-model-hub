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

from .helpers import by_coll, codes, crow, ctx, parsed, seq_ids, trow


def test_crea_space_subject_canvas_esquema_tabla_columna_y_vista_en_orden_dentro_del_proyecto():
    """Doc 87 §3.4: orden de ESCRITURA = cada colección después de las que
    referencia (canvases al final: referencian tablas Y vistas)."""
    p = parsed(tables=[trow(3, "Cliente", project="P", space="S", subject="SA", diagram="D", schema="ddv")],
               columns=[crow(3, "Cliente", "Codigo", data_type="STRING", pk=True)])
    plan = build_plan(p, ctx(), new_id=seq_ids())
    assert plan.has_errors is False and plan.report["warnings"] == []
    colls = [c["collection"] for c in plan.changes]
    assert colls == ["folders", "folders", "schemas", "schemas", "canonical_tables", "canonical_columns", "views", "subject_areas"]
    assert all(c["op"] == "upsert" for c in plan.changes)          # doc 87 §3.6: nunca destructivo
    b = by_coll(plan)
    assert "projects" not in b                       # doc 75: el proyecto es el del changeset
    space, subject = b["folders"]
    canvas, = b["subject_areas"]
    schema, view_schema = b["schemas"]
    table, = b["canonical_tables"]
    view, = b["views"]
    assert (space["payload"]["name"], space["payload"]["parentFolderId"], space["payload"]["projectId"]) == ("S", None, "p1")
    assert (subject["payload"]["name"], subject["payload"]["parentFolderId"]) == ("SA", space["entityId"])
    assert (canvas["payload"]["name"], canvas["payload"]["projectId"], canvas["payload"]["folderId"]) == ("D", "p1", subject["entityId"])
    assert canvas["payload"]["tableIds"] == [table["entityId"]]
    assert canvas["payload"]["viewIds"] == [view["entityId"]]                # doc 87 D4: canvas nuevo materializa la lista
    assert set(canvas["payload"]["layout"]) == {table["entityId"], view["entityId"]}
    assert schema["payload"] == {"id": schema["entityId"], "projectId": "p1", "name": "ddv", "description": None, "kind": "tables"}
    assert view_schema["payload"] == {"id": view_schema["entityId"], "projectId": "p1", "name": "ddv_vu", "description": None, "kind": "views"}
    assert table["payload"]["schema"] == "ddv" and table["payload"]["physicalName"] == "CLIENTE"
    assert table["payload"]["projectId"] == "p1"
    assert (view["payload"]["name"], view["payload"]["schema"], view["payload"]["sourceTableIds"]) == ("CLIENTE", "ddv_vu", [table["entityId"]])
    assert plan.affected_canvas_ids == [canvas["entityId"]]
    s = plan.report["summary"]
    assert (s["projects"]["create"], s["folders"]["create"], s["canvases"]["create"], s["schemas"]["create"], s["tables"]["create"],
            s["columns"]["create"], s["views"]["create"]) == (0, 2, 1, 2, 1, 1, 1)


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
    assert codes(plan, "warning") == ["existing-canvas", "view-no-columns"]
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
    assert codes(plan, "warning") == ["view-no-columns"]           # sin columnas no hay vista que crear


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
    assert codes(plan, "warning") == ["view-no-columns", "no-canvas"]
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


# ── Doc 87 §3.5: proyecto destino (capa de proyectos internos, D1) ─────────
from app.features.bulk_upload.plan_structure import resolve_base_folder, upload_targets  # noqa: E402


def _f(fid, name, parent=None, order=0):
    return {"id": fid, "projectId": "p1", "parentFolderId": parent, "name": name, "order": order}


def _ddv():
    return [_f("c", "CPYBCA", order=1), _f("o", "Otros", order=0), _f("x", "SA1", "c"), _f("y", "SA2", "o"),
            _f("loose", "Suelta")]


def test_upload_targets_none_auto_choose_con_conteos_y_orden():
    assert upload_targets([]) == {"mode": "none", "candidates": []}
    assert upload_targets([_f("f1", "SA")])["mode"] == "none"        # raíz sin subcarpetas no es capa
    t = upload_targets([_f("f1", "Modelo Fisico"), _f("f2", "1. Party", "f1")], canvases=[{"id": "sa", "folderId": "f2"}])
    assert t == {"mode": "auto", "candidates": [{"id": "f1", "name": "Modelo Fisico", "folders": 1, "canvases": 0}]}
    t = upload_targets(_ddv(), canvases=[{"folderId": "c"}, {"folderId": "c"}])
    assert t["mode"] == "choose" and [c["name"] for c in t["candidates"]] == ["Otros", "CPYBCA"]   # por `order`
    assert t["candidates"][1] == {"id": "c", "name": "CPYBCA", "folders": 1, "canvases": 2}


def test_resolve_base_folder_por_modo():
    none = {"mode": "none", "candidates": []}
    assert resolve_base_folder(none, None) == (None, None)
    assert resolve_base_folder(none, "f1") == (None, "target-folder-invalid")
    auto = {"mode": "auto", "candidates": [{"id": "f1"}]}
    assert resolve_base_folder(auto, None) == ("f1", None)
    assert resolve_base_folder(auto, "f1") == ("f1", None)
    assert resolve_base_folder(auto, "zz") == (None, "target-folder-invalid")
    choose = {"mode": "choose", "candidates": [{"id": "a"}, {"id": "b"}]}
    assert resolve_base_folder(choose, None) == (None, "target-folder-required")
    assert resolve_base_folder(choose, " b ") == ("b", None)
    assert resolve_base_folder(choose, "zz") == (None, "target-folder-invalid")


def test_con_una_sola_raiz_con_subcarpetas_el_subject_cuelga_de_ella_sin_pedir_destino():
    c = ctx(folders=[_f("f1", "Modelo Fisico"), _f("f2", "1. Party", "f1")])
    plan = build_plan(parsed(tables=[trow(3, "Cliente", subject="Prueba", diagram="D", schema="ddv")]), c, new_id=seq_ids())
    assert plan.has_errors is False
    folder, = by_coll(plan)["folders"]
    assert (folder["payload"]["name"], folder["payload"]["parentFolderId"]) == ("Prueba", "f1")
    canvas, = by_coll(plan)["subject_areas"]
    assert canvas["payload"]["folderId"] == folder["entityId"]
    # SUBJECT existente bajo la base se reusa (no se crea otro «1. Party» en la raíz)
    plan = build_plan(parsed(tables=[trow(3, "Cliente", subject="1. party", diagram="D", schema="ddv")]), c)
    assert "folders" not in by_coll(plan)
    assert by_coll(plan)["subject_areas"][0]["payload"]["folderId"] == "f2"


def test_con_dos_raices_el_destino_es_obligatorio_valida_y_manda():
    c = ctx(folders=_ddv())
    p = parsed(tables=[trow(3, "Cliente", subject="Prueba", diagram="D", schema="ddv")])
    plan = build_plan(p, c)
    assert codes(plan, "error") == ["target-folder-required"]
    assert "Otros, CPYBCA" in plan.report["errors"][0]["message"] and plan.report["errors"][0]["row"] is None
    assert codes(build_plan(p, c, target_folder_id="zz"), "error") == ["target-folder-invalid"]
    plan = build_plan(p, c, target_folder_id="o", new_id=seq_ids())
    assert plan.has_errors is False
    folder, = by_coll(plan)["folders"]
    assert (folder["payload"]["name"], folder["payload"]["parentFolderId"]) == ("Prueba", "o")
    plan = build_plan(parsed(tables=[trow(3, "Cliente", subject="sa2", diagram="D", schema="ddv")]), c, target_folder_id="o")
    assert "folders" not in by_coll(plan) and by_coll(plan)["subject_areas"][0]["payload"]["folderId"] == "y"


def test_space_cuelga_del_destino_y_un_diagrama_sin_carpetas_tambien():
    c = ctx(folders=_ddv())
    plan = build_plan(parsed(tables=[trow(3, "Cliente", space="Esp", subject="Sub", diagram="D", schema="ddv")]),
                      c, target_folder_id="c", new_id=seq_ids())
    space, subject = by_coll(plan)["folders"]
    assert (space["payload"]["parentFolderId"], subject["payload"]["parentFolderId"]) == ("c", space["entityId"])
    plan = build_plan(parsed(tables=[trow(3, "Cliente", diagram="D", schema="ddv")]), c, target_folder_id="c")
    assert by_coll(plan)["subject_areas"][0]["payload"]["folderId"] == "c"


def test_sin_capa_un_destino_enviado_es_error_y_todo_sigue_en_la_raiz():
    plan = build_plan(parsed(tables=[trow(3, "Cliente", subject="SA", diagram="D", schema="ddv")]), ctx(), target_folder_id="f1")
    assert codes(plan, "error") == ["target-folder-invalid"]
    assert by_coll(plan)["folders"][0]["payload"]["parentFolderId"] is None


def test_canvas_existente_que_gana_tablas_conserva_sus_trazos():
    """Doc 99: el planner re-arma el canvas con `SubjectAreaDoc`; los trazos
    manuales de wires (`routes`) viajan en el payload — sin declararlos en el
    modelo, la carga los borraría en silencio. (El arrange posterior de los
    canvases afectados los reinicia a propósito: eso lo decide el front.)"""
    routes = {"rel-1": [{"x": 200, "y": 40}, {"x": 200, "y": 160}]}
    c = ctx(canvases=[{"id": "sa1", "projectId": "p1", "folderId": None, "name": "D", "tableIds": ["t0"],
                       "layout": {"t0": {"x": 40, "y": 1000}}, "drawings": [], "udpValues": {},
                       "routes": routes}],
            tables=[{"id": "t0", "physicalName": "VIEJA", "logicalName": "Vieja", "schema": "ddv"}])
    p = parsed(tables=[trow(3, "Nueva", diagram="D", schema="ddv")],
               columns=[crow(3, "Nueva", "Codigo", data_type="STRING", pk=True)])
    plan = build_plan(p, c, new_id=seq_ids())
    canvas, = by_coll(plan)["subject_areas"]
    assert canvas["payload"]["routes"] == routes
    assert len(canvas["payload"]["tableIds"]) == 2


def test_canvas_nuevo_nace_sin_trazos():
    p = parsed(tables=[trow(3, "Cliente", diagram="D", schema="ddv")],
               columns=[crow(3, "Cliente", "Codigo", data_type="STRING", pk=True)])
    canvas, = by_coll(build_plan(p, ctx(), new_id=seq_ids()))["subject_areas"]
    assert canvas["payload"]["routes"] == {}
