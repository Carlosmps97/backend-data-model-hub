"""Planner — vistas `_vu` automáticas (doc 87 §3.1-3.3, D1-D4): vista normal
siempre y DAC condicional, esquema `<esquema>_vu` (reuso CI / alta `kind:
views`), réplica de TODAS las columnas efectivas en orden de display (doc 81),
vista existente intacta, membresía en el canvas de la tabla. Puro."""
from __future__ import annotations

from app.features.bulk_upload.planner import build_plan

from .helpers import by_coll, codes, crow, ctx, naming, parsed, seq_ids, trow

SCHEMAS = [{"id": "s1", "name": "ddv", "kind": "tables"}]
CLASS_P = {"id": "cl-p", "name": "Clasificacion del Dato", "level": "table", "view": "physical", "dataType": "list",
           "defaultValue": "No Definido", "allowedValues": ["No Definido", "No DAC", "DAC"]}
CLASS_L = {**CLASS_P, "id": "cl-l", "view": "logical"}
UDP_MAP = {"Clasificacion_del_Dato": [CLASS_P, CLASS_L]}


def _table(tid="t1", physical="CLIENTE", logical="Cliente", schema="ddv", udp=None, description=None):
    return {"id": tid, "projectId": "p1", "physicalName": physical, "logicalName": logical, "schema": schema,
            "description": description, "udpValues": udp or {}}


def _col(cid, physical, ordinal, pk=False, table="t1"):
    return {"id": cid, "projectId": "p1", "tableId": table, "physicalName": physical, "logicalName": physical.title(),
            "parentDomainId": None, "dataType": "STRING", "typeOverridden": False,
            "isPrimaryKey": True if pk else None, "isForeignKey": None, "isNullable": not pk,
            "isPartition": False, "description": None, "ordinal": ordinal, "udpValues": {}}


def _views(plan):
    return [c["payload"] for c in by_coll(plan).get("views", [])]


def _canvas(name="D", table_ids=(), view_ids=None, folder=None):
    doc = {"id": "sa1", "projectId": "p1", "folderId": folder, "name": name, "tableIds": list(table_ids),
           "layout": {t: {"x": 1, "y": 2} for t in table_ids}, "drawings": [], "udpValues": {}}
    if view_ids is not None:
        doc["viewIds"] = list(view_ids)
    return doc


def test_tabla_nueva_con_columnas_crea_la_vista_normal_espejo_en_un_esquema_vu_nuevo():
    p = parsed(tables=[trow(3, "Cliente", schema="ddv", diagram="D", description="Def cliente")],
               columns=[crow(3, "Cliente", "Nombre", data_type="STRING"),
                        crow(4, "Cliente", "Codigo", data_type="STRING", pk=True)])
    plan = build_plan(p, ctx(schemas=SCHEMAS), new_id=seq_ids())
    assert plan.has_errors is False and plan.report["warnings"] == []
    b = by_coll(plan)
    tid = b["canonical_tables"][0]["entityId"]
    (view,) = _views(plan)
    assert (view["name"], view["schema"], view["tableId"], view["sourceTableIds"]) == ("CLIENTE", "ddv_vu", tid, [tid])
    assert (view["showOnCanvas"], view["customSql"], view["udpValues"], view["projectId"]) == (True, None, {}, "p1")
    assert view["description"] == "Def cliente"                       # S5: hereda la definición de la tabla
    assert [s["column"] for s in view["sources"]] == ["CODIGO", "NOMBRE"]   # PK primero (doc 81)
    assert all(s["outputAlias"] == s["column"] and s["tableId"] == tid for s in view["sources"])
    (vschema,) = [s for s in b["schemas"] if s["payload"]["name"] == "ddv_vu"]
    assert vschema["payload"]["kind"] == "views"
    (canvas,) = b["subject_areas"]
    assert canvas["payload"]["viewIds"] == [view["id"]] and view["id"] in canvas["payload"]["layout"]
    assert plan.report["summary"]["views"] == {"create": 1, "update": 0, "unchanged": 0}
    assert plan.report["summary"]["schemas"] == {"create": 1, "update": 0, "unchanged": 1}   # ddv reusado + ddv_vu nuevo
    assert plan.report["tables"][0]["views"] == {"create": 1, "update": 0, "unchanged": 0}


def test_tabla_dac_crea_ademas_la_vista_dac_con_las_mismas_columnas():
    p = parsed(tables=[trow(3, "Cliente", schema="ddv", udp={"Clasificacion_del_Dato": "dac"})],
               columns=[crow(3, "Cliente", "Codigo", data_type="STRING")], table_udp=UDP_MAP)
    plan = build_plan(p, ctx(schemas=SCHEMAS, udp_defs=[CLASS_P, CLASS_L]), new_id=seq_ids())
    normal, dac = _views(plan)
    assert (normal["name"], dac["name"]) == ("CLIENTE", "CLIENTEDAC")        # D2: sufijo sin guion bajo
    assert normal["schema"] == dac["schema"] == "ddv_vu"
    assert dac["sources"] == normal["sources"]                              # sin transformación por DAC
    assert plan.report["summary"]["views"]["create"] == 2
    assert plan.report["tables"][0]["views"]["create"] == 2


def test_no_dac_o_sin_def_en_el_proyecto_no_crea_vista_dac():
    p = parsed(tables=[trow(3, "Cliente", schema="ddv", udp={"Clasificacion_del_Dato": "No DAC"})],
               columns=[crow(3, "Cliente", "Codigo", data_type="STRING")], table_udp=UDP_MAP)
    assert [v["name"] for v in _views(build_plan(p, ctx(schemas=SCHEMAS, udp_defs=[CLASS_P, CLASS_L])))] == ["CLIENTE"]
    p = parsed(tables=[trow(3, "Cliente", schema="ddv", udp={"Clasificacion_del_Dato": "DAC"})],
               columns=[crow(3, "Cliente", "Codigo", data_type="STRING")], table_udp=UDP_MAP)
    assert [v["name"] for v in _views(build_plan(p, ctx(schemas=SCHEMAS)))] == ["CLIENTE"]   # sin def ⇒ nunca DAC


def test_la_faceta_fisica_decide_y_la_logica_es_fallback():
    cols = {"t1": [_col("c1", "COD", 0)]}
    fisica_manda = ctx(schemas=SCHEMAS, udp_defs=[CLASS_P, CLASS_L], columns_by_table=cols,
                       tables=[_table(udp={"cl-p": "No DAC", "cl-l": "DAC"})])
    assert [v["name"] for v in _views(build_plan(parsed(tables=[trow(3, "Cliente")]), fisica_manda))] == ["CLIENTE"]
    solo_logica = ctx(schemas=SCHEMAS, udp_defs=[CLASS_P, CLASS_L], columns_by_table=cols,
                      tables=[_table(udp={"cl-l": "DAC"})])
    assert [v["name"] for v in _views(build_plan(parsed(tables=[trow(3, "Cliente")]), solo_logica))] == ["CLIENTE", "CLIENTEDAC"]


def test_sufijo_dac_sigue_el_case_del_naming_de_tablas():
    lower = naming()
    lower["table"]["case"] = "lower"
    c = ctx(naming=lower, schemas=SCHEMAS, udp_defs=[CLASS_P], columns_by_table={"t1": [_col("c1", "cod", 0)]},
            tables=[_table(physical="cliente", udp={"cl-p": "DAC"})])
    assert [v["name"] for v in _views(build_plan(parsed(tables=[trow(3, "Cliente")]), c))] == ["cliente", "clientedac"]


def test_tabla_existente_sin_cambios_gana_su_vista_con_las_columnas_efectivas_en_orden_unico():
    """Doc 94: el orden único — PK primero y cada bloque por ordinal (sin orden de llave aparte)."""
    c = ctx(schemas=SCHEMAS, tables=[_table(description="Def")],
            columns_by_table={"t1": [_col("c1", "B", 0), _col("c2", "K2", 1, pk=True), _col("c3", "K1", 2, pk=True)]})
    plan = build_plan(parsed(tables=[trow(3, "Cliente")]), c, new_id=seq_ids())
    assert [ch["collection"] for ch in plan.changes] == ["schemas", "views"]     # la tabla queda intacta
    (view,) = _views(plan)
    assert [s["column"] for s in view["sources"]] == ["K2", "K1", "B"] and view["description"] == "Def"
    assert plan.report["summary"]["tables"] == {"create": 0, "update": 0, "unchanged": 1}
    assert plan.report["summary"]["views"] == {"create": 1, "update": 0, "unchanged": 0}


def test_las_columnas_efectivas_mezclan_existentes_actualizadas_y_nuevas():
    """S4: la vista replica TODAS las columnas de la tabla (las de la BD que la
    hoja no menciona, las que actualiza y las nuevas), no solo las de la hoja."""
    cols = [_col("c1", "COD", 0), _col("c2", "OTRA", 1)]
    cols[0]["logicalName"] = "Codigo"
    c = ctx(schemas=SCHEMAS, tables=[_table()], columns_by_table={"t1": cols})
    p = parsed(tables=[trow(3, "Cliente")],
               columns=[crow(3, "Cliente", "Codigo", data_type="STRING", description="def"),   # actualiza c1 (por lógico)
                        crow(4, "Cliente", "Nombre", data_type="STRING")])                     # nueva
    plan = build_plan(p, c, new_id=seq_ids())
    assert plan.report["summary"]["columns"] == {"create": 1, "update": 1, "unchanged": 0}
    (view,) = _views(plan)
    assert [s["column"] for s in view["sources"]] == ["COD", "OTRA", "NOMBRE"]


def test_vista_existente_por_nombre_y_esquema_ci_no_se_toca_y_reusa_la_grafia_del_esquema():
    c = ctx(schemas=SCHEMAS + [{"id": "s2", "name": "DDV_VU", "kind": "views"}], udp_defs=[CLASS_P],
            tables=[_table(udp={"cl-p": "DAC"})], columns_by_table={"t1": [_col("c1", "COD", 0)]},
            views=[{"id": "v1", "name": "cliente", "schema": "ddv_vu", "sourceTableIds": ["t1"]}])
    plan = build_plan(parsed(tables=[trow(3, "Cliente")]), c, new_id=seq_ids())
    assert [ch["collection"] for ch in plan.changes] == ["views"]              # solo la DAC que faltaba; sin esquema nuevo
    (dac,) = _views(plan)
    assert (dac["name"], dac["schema"]) == ("CLIENTEDAC", "DDV_VU")           # grafía exacta del catálogo
    assert plan.report["summary"]["views"] == {"create": 1, "update": 0, "unchanged": 1}
    assert plan.report["tables"][0]["views"] == {"create": 1, "update": 0, "unchanged": 1}
    assert codes(plan, "warning") == []                                       # D3: silencio, no es un cambio


def test_esquema_vu_catalogado_como_de_tablas_es_error_de_la_fila():
    c = ctx(schemas=SCHEMAS + [{"id": "s2", "name": "ddv_vu", "kind": "tables"}])
    p = parsed(tables=[trow(3, "Cliente", schema="ddv")], columns=[crow(3, "Cliente", "Codigo", data_type="STRING")])
    plan = build_plan(p, c)
    (e,) = plan.report["errors"]
    assert (e["code"], e["row"], e["column"]) == ("view-schema-kind", 3, "ESQUEMA") and "ddv_vu" in e["message"]
    assert _views(plan) == [] and plan.has_errors is True


def test_sufijo_del_esquema_sigue_el_case_de_la_base():
    c = ctx(schemas=[{"id": "s1", "name": "DDV", "kind": "tables"}])
    p = parsed(tables=[trow(3, "Cliente", schema="DDV")], columns=[crow(3, "Cliente", "Codigo", data_type="STRING")])
    plan = build_plan(p, c)
    assert _views(plan)[0]["schema"] == "DDV_VU"


def test_canvas_existente_con_lista_anexa_la_vista_y_uno_legacy_no_materializa():
    base = dict(schemas=SCHEMAS, tables=[_table()], columns_by_table={"t1": [_col("c1", "COD", 0)]})
    con_lista = ctx(canvases=[_canvas(table_ids=["t1"], view_ids=["v0"])], **base)
    plan = build_plan(parsed(tables=[trow(3, "Cliente", diagram="D")]), con_lista, new_id=seq_ids())
    (canvas,) = by_coll(plan)["subject_areas"]
    (view,) = _views(plan)
    assert canvas["payload"]["viewIds"] == ["v0", view["id"]] and view["id"] in canvas["payload"]["layout"]
    assert canvas["payload"]["tableIds"] == ["t1"] and canvas["payload"]["layout"]["t1"] == {"x": 1, "y": 2}
    assert plan.affected_canvas_ids == ["sa1"]
    assert plan.report["summary"]["canvases"] == {"create": 0, "update": 1, "unchanged": 0}
    (w,) = plan.report["warnings"]
    assert w["code"] == "existing-canvas" and "1 view(s)" in w["message"] and "table" not in w["message"]
    legacy = ctx(canvases=[_canvas(table_ids=["t1"])], **base)
    plan = build_plan(parsed(tables=[trow(3, "Cliente", diagram="D")]), legacy, new_id=seq_ids())
    (canvas,) = by_coll(plan)["subject_areas"]
    assert canvas["payload"]["viewIds"] is None                        # S9: rige la regla vieja (showOnCanvas)
    assert _views(plan)[0]["id"] in canvas["payload"]["layout"]


def test_tabla_sin_columnas_avisa_y_tabla_implicita_no_genera_vistas():
    plan = build_plan(parsed(tables=[trow(3, "Cliente", schema="ddv")]), ctx(schemas=SCHEMAS))
    assert codes(plan, "warning") == ["view-no-columns", "no-canvas"] and _views(plan) == []
    c = ctx(schemas=SCHEMAS, tables=[_table()], columns_by_table={"t1": [_col("c1", "COD", 0)]})
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Nuevo", data_type="STRING")]), c)   # S3: solo hoja de columnas
    assert _views(plan) == [] and plan.report["summary"]["views"] == {"create": 0, "update": 0, "unchanged": 0}


def test_tabla_sin_esquema_avisa_y_no_crea_vista():
    c = ctx(tables=[_table(schema=None)], columns_by_table={"t1": [_col("c1", "COD", 0)]})
    plan = build_plan(parsed(tables=[trow(3, "Cliente")]), c)
    assert codes(plan, "warning") == ["view-no-schema"] and _views(plan) == []


# ── Doc 102 (D3): tabla DAC — la vista normal va SIN las columnas DAC-* ──────
COL_P = {"id": "ccl-p", "name": "Clasificacion del Dato", "level": "column", "view": "physical", "dataType": "list",
         "defaultValue": "No Definido",
         "allowedValues": ["No Definido", "No DAC", "DAC-DOCUMENTO", "DAC-NOMBRE"]}
COL_L = {**COL_P, "id": "ccl-l", "view": "logical"}
COL_MAP = {"UDP_Clasificacion_del_Dato": [COL_P, COL_L]}
DEFS = [CLASS_P, CLASS_L, COL_P, COL_L]


def _dac_plan(table_class="DAC", cols=None):
    cols = cols or [crow(3, "Cliente", "Codigo", data_type="STRING", pk=True, udp={"UDP_Clasificacion_del_Dato": "No DAC"}),
                    crow(4, "Cliente", "Documento", data_type="STRING", udp={"UDP_Clasificacion_del_Dato": "dac-documento"}),
                    crow(5, "Cliente", "Nombre", data_type="STRING", udp={"UDP_Clasificacion_del_Dato": "DAC-NOMBRE"})]
    p = parsed(tables=[trow(3, "Cliente", schema="ddv", udp={"Clasificacion_del_Dato": table_class})],
               columns=cols, table_udp=UDP_MAP, column_udp=COL_MAP)
    return build_plan(p, ctx(schemas=SCHEMAS, udp_defs=DEFS), new_id=seq_ids())


def test_tabla_dac_vista_normal_sin_columnas_dac_y_la_dac_con_todas_doc102():
    plan = _dac_plan()
    normal, dac = _views(plan)
    assert (normal["name"], dac["name"]) == ("CLIENTE", "CLIENTEDAC")
    assert [s["column"] for s in normal["sources"]] == ["CODIGO"]
    assert [s["column"] for s in dac["sources"]] == ["CODIGO", "DOCUMENTO", "NOMBRE"]
    assert codes(plan, "warning") == ["no-canvas"]


def test_tabla_no_dac_conserva_todas_las_columnas_aunque_tenga_dac_doc102():
    (normal,) = _views(_dac_plan(table_class="No DAC"))
    assert [s["column"] for s in normal["sources"]] == ["CODIGO", "DOCUMENTO", "NOMBRE"]


def test_tabla_dac_solo_con_columnas_dac_avisa_y_no_crea_la_normal_doc102():
    plan = _dac_plan(cols=[crow(3, "Cliente", "Documento", data_type="STRING",
                                udp={"UDP_Clasificacion_del_Dato": "DAC-DOCUMENTO"})])
    assert [v["name"] for v in _views(plan)] == ["CLIENTEDAC"]
    assert "view-only-dac-columns" in codes(plan, "warning")


def test_columna_dac_por_faceta_logica_de_respaldo_doc102():
    cols = {"t1": [dict(_col("c1", "COD", 0, pk=True), udpValues={"ccl-p": "No DAC"}),
                   dict(_col("c2", "DOC", 1), udpValues={"ccl-l": "DAC-DOCUMENTO"}),          # solo lógica
                   dict(_col("c3", "NOM", 2), udpValues={"ccl-p": "No DAC", "ccl-l": "DAC-NOMBRE"})]}   # física manda
    c = ctx(schemas=SCHEMAS, udp_defs=DEFS, columns_by_table=cols, tables=[_table(udp={"cl-p": "DAC"})])
    normal, dac = _views(build_plan(parsed(tables=[trow(3, "Cliente")]), c, new_id=seq_ids()))
    assert [s["column"] for s in normal["sources"]] == ["COD", "NOM"]
    assert [s["column"] for s in dac["sources"]] == ["COD", "DOC", "NOM"]
