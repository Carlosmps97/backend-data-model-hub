"""Doc 78 §3.5: mustExist (no crear) con severidad, onExisting reject, defaults
de campo solo en entidades nuevas, y el `column` de la incidencia usa la
cabecera REAL del perfil."""
from __future__ import annotations

from app.features.bulk_upload.planner import PlanOptions, build_plan

from .helpers import by_coll, codes, crow, ctx, parsed, trow

_SCHEMAS = [{"id": "s1", "name": "ddv", "kind": "tables"}]
EXISTING = [{"id": "t1", "projectId": "p1", "physicalName": "CLIENTE", "logicalName": "Cliente", "schema": "ddv",
             "description": None, "udpValues": {}}]
COL = {"id": "c1", "projectId": "p1", "tableId": "t1", "physicalName": "X", "logicalName": "X", "parentDomainId": None,
       "dataType": "STRING", "typeOverridden": False, "isPrimaryKey": None, "pkPosition": None, "isForeignKey": None,
       "isNullable": True, "isPartition": False, "description": None, "ordinal": 0, "udpValues": {}}


def test_must_exist_error_no_crea_y_warning_crea_avisando():
    p = parsed(tables=[trow(3, "Cliente", schema="nuevo", subject="Ventas")], headers={"tables": {"schema": "ESQ", "subject": "SUBJ"}})
    opts = PlanOptions(must_exist={"schema": "error", "subject": "warning"})
    plan = build_plan(p, ctx(schemas=_SCHEMAS), options=opts)
    (e,) = plan.report["errors"]
    assert (e["code"], e["column"], e["row"]) == ("must-exist", "ESQ", 3) and "canonical_tables" not in by_coll(plan)
    p2 = parsed(tables=[trow(3, "Cliente", schema="ddv", subject="Ventas", diagram="D")], headers={"tables": {"subject": "SUBJ"}})
    plan2 = build_plan(p2, ctx(schemas=_SCHEMAS), options=PlanOptions(must_exist={"subject": "warning"}))
    assert plan2.has_errors is False and ("must-exist", "SUBJ") in [(w["code"], w["column"]) for w in plan2.report["warnings"]]
    assert len(by_coll(plan2)["folders"]) == 1 and len(by_coll(plan2)["subject_areas"]) == 1


def test_must_exist_en_diagrama_corta_la_membresia_pero_crea_la_tabla():
    p = parsed(tables=[trow(3, "Cliente", schema="ddv", diagram="Nuevo")], headers={"tables": {"diagram": "DIAG"}})
    plan = build_plan(p, ctx(schemas=_SCHEMAS), options=PlanOptions(must_exist={"diagram": "error"}))
    assert [(e["code"], e["column"]) for e in plan.report["errors"]] == [("must-exist", "DIAG")]
    assert "subject_areas" not in by_coll(plan) and len(by_coll(plan)["canonical_tables"]) == 1


def test_on_existing_reject_tabla_y_columna():
    p = parsed(tables=[trow(3, "Cliente", physical="CLIENTE", schema="ddv")], columns=[crow(3, "Cliente", "Y", data_type="STRING")])
    plan = build_plan(p, ctx(schemas=_SCHEMAS, tables=EXISTING), options=PlanOptions(on_existing_table="reject"))
    assert codes(plan, "error") == ["existing-not-allowed"] and plan.report["errors"][0]["column"] == "TABLA_LOGICO"
    p2 = parsed(tables=[trow(3, "Cliente", physical="CLIENTE")], columns=[crow(3, "Cliente", "X"), crow(4, "Cliente", "Z", data_type="STRING")])
    plan2 = build_plan(p2, ctx(schemas=_SCHEMAS, tables=EXISTING, columns_by_table={"t1": [COL]}),
                       options=PlanOptions(on_existing_column="reject"))
    assert [(e["code"], e["row"]) for e in plan2.report["errors"]] == [("existing-not-allowed", 3)]
    assert [c["payload"]["logicalName"] for c in by_coll(plan2)["canonical_columns"]] == ["Z"]


def test_default_de_campo_solo_para_entidad_nueva():
    nueva = parsed(tables=[trow(3, "Nueva", defaults={"schema": "ddv"})])
    assert by_coll(build_plan(nueva, ctx(schemas=_SCHEMAS)))["canonical_tables"][0]["payload"]["schema"] == "ddv"
    vieja = parsed(tables=[trow(3, "Cliente", physical="CLIENTE", description="d", defaults={"schema": "otro"})])
    plan = build_plan(vieja, ctx(schemas=_SCHEMAS, tables=EXISTING))
    assert by_coll(plan)["canonical_tables"][0]["payload"]["schema"] == "ddv"
    col = parsed(tables=[trow(3, "Cliente", schema="ddv")],
                 columns=[crow(3, "Cliente", "A", defaults={"data_type": "BIGINT", "pk": "X"})])
    (c,) = [x["payload"] for x in by_coll(build_plan(col, ctx(schemas=_SCHEMAS)))["canonical_columns"]]
    assert (c["dataType"], c["isPrimaryKey"], c["pkPosition"]) == ("BIGINT", True, 0)


def test_column_de_incidencia_usa_cabecera_del_perfil():
    p = parsed(tables=[trow(3, "")], headers={"tables": {"logicalName": "Nombre Lógico"}})
    (e,) = build_plan(p, ctx()).report["errors"]
    assert e["column"] == "Nombre Lógico" and "Nombre Lógico" in e["message"]
    p2 = parsed(tables=[trow(3, "Cliente", schema="ddv")], columns=[crow(3, "Cliente", "A")], headers={"columns": {"dataType": "Tipo"}})
    (e2,) = build_plan(p2, ctx(schemas=_SCHEMAS)).report["errors"]
    assert (e2["code"], e2["column"]) == ("missing-required", "Tipo")


def test_from_profile_lee_must_exist_y_politicas():
    profile = {"sheets": {"tables": {"mappings": [
        {"header": "ESQUEMA", "target": {"kind": "field", "field": "schema"}, "rules": [{"type": "mustExist", "severity": "warning"}]},
        {"header": "U", "target": {"kind": "udp", "udpIds": ["x"]}, "rules": [{"type": "mustExist"}]}]},
        "columns": {"mappings": [{"header": "PD", "target": {"kind": "field", "field": "parentDomain"}, "rules": [{"type": "mustExist"}]}]}},
        "policies": {"onExistingTable": "reject"}}
    o = PlanOptions.from_profile(profile)
    assert o.must_exist == {"schema": "warning", "parentDomain": "error"}
    assert (o.on_existing_table, o.on_existing_column) == ("reject", "update")
