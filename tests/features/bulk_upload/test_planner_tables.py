"""Planner — tablas (doc 55 §3.3-3.4, §4.1, §5): identidad, físico derivado o
declarado, longitud, esquema, UDP, duplicados, unchanged y warnings. Puro."""
from __future__ import annotations

from app.features.bulk_upload.planner import build_plan

from .helpers import by_coll, codes, ctx, naming, parsed, seq_ids, trow


def _existing(tid="t1", physical="CLIENTE", logical="Cliente", schema="ddv", description=None, udp=None):
    return {"id": tid, "projectId": "p1", "physicalName": physical, "logicalName": logical, "schema": schema,
            "description": description, "udpValues": udp or {}}


def _schemas():
    return [{"id": "s1", "name": "ddv", "kind": "tables"}]


def _udp_universal():
    return [{"id": "u1", "name": "Universal", "level": "table", "dataType": "list",
             "defaultValue": "No Definido", "allowedValues": ["No Definido", "Si", "No"]}]


def _table_change(plan):
    (ch,) = by_coll(plan)["canonical_tables"]
    return ch


def test_fisico_derivado_con_glosario_join_upper():
    c = ctx(schemas=_schemas(), glossary={"table": {"codigo": "COD", "cliente": "CLI"}, "column": {}})
    plan = build_plan(parsed(tables=[trow(3, "Codigo Cliente", schema="ddv")]), c)
    assert _table_change(plan)["payload"]["physicalName"] == "CODCLI"


def test_fisico_declarado_manda_y_conserva_grafia():
    plan = build_plan(parsed(tables=[trow(3, "Cliente", physical="Mi_Tabla", schema="ddv")]), ctx(schemas=_schemas()))
    assert _table_change(plan)["payload"]["physicalName"] == "Mi_Tabla"


def test_identidad_por_fisico_ci_actualiza_con_doc_completo():
    c = ctx(schemas=_schemas(), tables=[_existing()])
    plan = build_plan(parsed(tables=[trow(3, "Cliente", physical="cliente", description="Def")]), c)
    ch = _table_change(plan)
    assert ch["entityId"] == "t1"
    assert ch["payload"] == {"id": "t1", "projectId": "p1", "physicalName": "cliente", "logicalName": "Cliente", "schema": "ddv",
                             "physicalNameOverridden": False,
                             "logicalOnly": False, "physicalOnly": False,   # doc 69 (facetas)
                             "description": "Def", "udpValues": {}}
    assert codes(plan, "warning") == ["rename", "existing-table"]   # físico cambia de grafía
    assert plan.report["summary"]["tables"] == {"create": 0, "update": 1, "unchanged": 0}


def test_update_sin_fisico_declarado_conserva_el_fisico_existente():
    c = ctx(schemas=_schemas(), tables=[_existing()])
    plan = build_plan(parsed(tables=[trow(3, "Cliente", description="Def")]), c)
    ch = _table_change(plan)
    assert ch["payload"]["physicalName"] == "CLIENTE"
    assert codes(plan, "warning") == ["existing-table"]
    assert "description" in plan.report["warnings"][0]["message"]


def test_fallback_por_logico_unico_conserva_fisico_y_avisa():
    c = ctx(schemas=_schemas(), tables=[_existing(physical="CLI")])
    plan = build_plan(parsed(tables=[trow(3, "Cliente", description="Def")]), c)
    ch = _table_change(plan)
    assert ch["entityId"] == "t1" and ch["payload"]["physicalName"] == "CLI"
    assert codes(plan, "warning") == ["matched-by-logical", "existing-table"]


def test_logico_ambiguo_es_error():
    c = ctx(schemas=_schemas(), tables=[_existing("t1", "CLI1"), _existing("t2", "CLI2")])
    plan = build_plan(parsed(tables=[trow(3, "Cliente", description="Def")]), c)
    assert codes(plan, "error") == ["ambiguous-match"]
    assert "canonical_tables" not in by_coll(plan)


def test_fisico_declarado_nuevo_con_logico_existente_crea_y_avisa():
    c = ctx(schemas=_schemas(), tables=[_existing(physical="CLI")])
    plan = build_plan(parsed(tables=[trow(3, "Cliente", physical="CLIENTE2", schema="ddv")]), c, new_id=seq_ids())
    ch = _table_change(plan)
    assert ch["entityId"] == "n1" and ch["payload"]["physicalName"] == "CLIENTE2"
    assert codes(plan, "warning") == ["logical-exists", "no-canvas"]


def test_nombre_fisico_derivado_muy_largo_es_error():
    c = ctx(schemas=_schemas(), naming=naming(max_len=10))
    plan = build_plan(parsed(tables=[trow(3, "Tabla de nombre muy largo", schema="ddv")]), c)
    (e,) = plan.report["errors"]
    assert (e["code"], e["column"], e["row"]) == ("name-too-long", "TABLA_LOGICO", 3)


def test_nombre_fisico_declarado_muy_largo_marca_la_columna_fisica():
    c = ctx(schemas=_schemas(), naming=naming(max_len=10))
    plan = build_plan(parsed(tables=[trow(3, "T", physical="NOMBRELARGUISIMO", schema="ddv")]), c)
    assert plan.report["errors"][0]["column"] == "TABLA_FISICA"


def test_nombre_largo_heredado_sin_cambios_no_penaliza():
    c = ctx(schemas=_schemas(), naming=naming(max_len=10), tables=[_existing(physical="NOMBRELARGUISIMO")])
    plan = build_plan(parsed(tables=[trow(3, "Cliente", physical="NOMBRELARGUISIMO", description="d")]), c)
    assert plan.has_errors is False and _table_change(plan)["payload"]["description"] == "d"


def test_esquema_obligatorio_al_crear():
    plan = build_plan(parsed(tables=[trow(3, "Cliente")]), ctx())
    (e,) = plan.report["errors"]
    assert (e["code"], e["column"]) == ("missing-required", "ESQUEMA")


def test_esquema_de_vistas_es_error():
    c = ctx(schemas=[{"id": "s1", "name": "ddv_vu", "kind": "views"}])
    plan = build_plan(parsed(tables=[trow(3, "Cliente", schema="ddv_vu")]), c)
    assert codes(plan, "error") == ["schema-kind"]


def test_esquema_nuevo_invalido_es_error():
    plan = build_plan(parsed(tables=[trow(3, "Cliente", schema="1abc")]), ctx())
    assert codes(plan, "error") == ["invalid-schema-name"]
    assert "schemas" not in by_coll(plan)


def test_esquema_vacio_en_update_conserva_el_existente():
    c = ctx(schemas=_schemas(), tables=[_existing()])
    plan = build_plan(parsed(tables=[trow(3, "Cliente", description="d")]), c)
    assert _table_change(plan)["payload"]["schema"] == "ddv"


def _udp_map():
    return {"UDP_Universal": _udp_universal()}


def test_udp_vacio_toma_el_default_al_crear():
    c = ctx(schemas=_schemas(), udp_defs=_udp_universal())
    plan = build_plan(parsed(tables=[trow(3, "Cliente", schema="ddv", udp={"UDP_Universal": ""})],
                             table_udp=_udp_map()), c)
    assert _table_change(plan)["payload"]["udpValues"] == {"u1": "No Definido"}
    assert plan.report["warnings"] == [] or codes(plan, "warning") == ["no-canvas"]


def test_udp_lista_valida_case_insensitive_y_guarda_grafia_canonica():
    c = ctx(schemas=_schemas(), udp_defs=_udp_universal())
    plan = build_plan(parsed(tables=[trow(3, "Cliente", schema="ddv", udp={"UDP_Universal": "  si "})],
                             table_udp=_udp_map()), c)
    assert _table_change(plan)["payload"]["udpValues"] == {"u1": "Si"}


def test_udp_valor_fuera_de_lista_es_error():
    c = ctx(schemas=_schemas(), udp_defs=_udp_universal())
    plan = build_plan(parsed(tables=[trow(3, "Cliente", schema="ddv", udp={"UDP_Universal": "Quizas"})],
                             table_udp=_udp_map()), c)
    (e,) = plan.report["errors"]
    assert (e["code"], e["column"], e["row"]) == ("invalid-udp-value", "UDP_Universal", 3)
    assert "No Definido, Si, No" in e["message"]
    assert "canonical_tables" not in by_coll(plan)


def test_udp_vacio_en_update_conserva_el_valor_existente_y_queda_unchanged():
    c = ctx(schemas=_schemas(), udp_defs=_udp_universal(), tables=[_existing(udp={"u1": "Si"})])
    plan = build_plan(parsed(tables=[trow(3, "Cliente", udp={"UDP_Universal": ""})],
                             table_udp=_udp_map()), c)
    assert plan.changes == []
    assert plan.report["summary"]["tables"] == {"create": 0, "update": 0, "unchanged": 1}


def test_udp_number_y_boolean():
    defs = [{"id": "n", "name": "Peso", "level": "table", "dataType": "number", "defaultValue": None, "allowedValues": []},
            {"id": "b", "name": "Activo", "level": "table", "dataType": "boolean", "defaultValue": None, "allowedValues": []}]
    c = ctx(schemas=_schemas(), udp_defs=defs)
    plan = build_plan(parsed(tables=[trow(3, "A", schema="ddv", udp={"UDP_Peso": "12.5", "UDP_Activo": "Si"}),
                                     trow(4, "B", schema="ddv", udp={"UDP_Peso": "abc", "UDP_Activo": "tal vez"})],
                             table_udp={"UDP_Peso": [defs[0]], "UDP_Activo": [defs[1]]}), c)
    a, = [ch for ch in by_coll(plan)["canonical_tables"] if ch["payload"]["logicalName"] == "A"]
    assert a["payload"]["udpValues"] == {"n": "12.5", "b": "true"}
    assert sorted((e["row"], e["column"]) for e in plan.report["errors"]) == [(4, "UDP_Activo"), (4, "UDP_Peso")]


def test_duplicado_en_archivo_es_error_en_la_segunda_fila():
    plan = build_plan(parsed(tables=[trow(3, "Cliente", schema="ddv"), trow(4, "cliente", schema="ddv")]), ctx(schemas=_schemas()))
    (e,) = plan.report["errors"]
    assert (e["code"], e["row"]) == ("duplicate-in-file", 4) and "row 3" in e["message"]
    assert len(by_coll(plan)["canonical_tables"]) == 1


def test_fila_sin_logico_es_error():
    plan = build_plan(parsed(tables=[trow(3, "", schema="ddv")]), ctx())
    (e,) = plan.report["errors"]
    assert (e["code"], e["column"]) == ("missing-required", "TABLA_LOGICO")


def test_rename_logico_por_fisico_declarado_avisa():
    c = ctx(schemas=_schemas(), tables=[_existing()])
    plan = build_plan(parsed(tables=[trow(3, "Cliente Final", physical="CLIENTE")]), c)
    assert _table_change(plan)["payload"]["logicalName"] == "Cliente Final"
    assert codes(plan, "warning") == ["rename", "existing-table"]


def test_desglose_por_tabla_en_el_reporte():
    c = ctx(schemas=_schemas(), tables=[_existing()])
    plan = build_plan(parsed(tables=[trow(3, "Cliente", project="P", diagram="D", description="d"),
                                     trow(4, "Nueva", schema="ddv")]), c)
    rows = plan.report["tables"]
    assert [(r["row"], r["action"], r["canvas"]) for r in rows] == [(3, "update", "P / D"), (4, "create", None)]
    assert rows[0]["issues"] == 1 and rows[1]["issues"] == 1    # existing-table / no-canvas
    assert rows[0]["columns"] == {"create": 0, "update": 0, "unchanged": 0}
