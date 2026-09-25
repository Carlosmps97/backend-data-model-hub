"""Doc 78 D2: una cabecera puede alimentar VARIAS defs (lógica y física, con
nombres distintos); cada def valida contra sus propios allowedValues. Doc 69:
las columnas nuevas heredan el tipo LÓGICO del dominio; las existentes
conservan sus campos de faceta."""
from __future__ import annotations

from app.features.bulk_upload.planner import build_plan

from .helpers import by_coll, crow, ctx, parsed, trow

_SCHEMAS = [{"id": "s1", "name": "ddv", "kind": "tables"}]
CP = {"id": "c-p", "name": "Campo Cross", "level": "column", "view": "physical", "dataType": "list",
      "allowedValues": ["No Definido", "Si", "No"]}
AL = {"id": "x-l", "name": "Atributo Cross", "level": "column", "view": "logical", "dataType": "list",
      "allowedValues": ["No Definido", "Si"]}
DOMAINS = [{"id": "d1", "name": "Codigo", "defaultDataType": "VARCHAR(30)", "logicalDataType": "VARCHAR(20)"}]


def _cols(plan):
    return [c["payload"] for c in by_coll(plan).get("canonical_columns", [])]


def test_una_cabecera_escribe_en_las_dos_defs():
    p = parsed(tables=[trow(3, "Cliente", schema="ddv")],
               columns=[crow(3, "Cliente", "A", data_type="STRING", udp={"UDP Campo Cross": "si"})],
               column_udp={"UDP Campo Cross": [CP, AL]})
    plan = build_plan(p, ctx(schemas=_SCHEMAS))
    assert plan.has_errors is False
    assert _cols(plan)[0]["udpValues"] == {"c-p": "Si", "x-l": "Si"}


def test_valor_valido_para_una_def_e_invalido_para_la_otra():
    p = parsed(tables=[trow(3, "Cliente", schema="ddv")],
               columns=[crow(3, "Cliente", "A", data_type="STRING", udp={"UDP Campo Cross": "no"})],
               column_udp={"UDP Campo Cross": [CP, AL]})
    plan = build_plan(p, ctx(schemas=_SCHEMAS))
    (e,) = plan.report["errors"]
    assert (e["code"], e["column"], "Atributo Cross" in e["message"]) == ("invalid-udp-value", "UDP Campo Cross", True)
    assert _cols(plan) == []


def test_default_del_mapeo_solo_en_entidad_nueva():
    p = parsed(tables=[trow(3, "Cliente", schema="ddv")],
               columns=[crow(3, "Cliente", "A", data_type="STRING", udp={"UDP Campo Cross": ""}, udp_defaults={"UDP Campo Cross": "Si"})],
               column_udp={"UDP Campo Cross": [CP]})
    assert _cols(build_plan(p, ctx(schemas=_SCHEMAS)))[0]["udpValues"] == {"c-p": "Si"}
    existing = {"id": "c1", "projectId": "p1", "tableId": "t1", "physicalName": "A", "logicalName": "A", "parentDomainId": None,
                "dataType": "STRING", "typeOverridden": False, "isPrimaryKey": None,
                "isForeignKey": None, "isNullable": True, "isPartition": False, "description": None, "ordinal": 0,
                "udpValues": {}}
    table = {"id": "t1", "projectId": "p1", "physicalName": "CLIENTE", "logicalName": "Cliente", "schema": "ddv",
             "description": None, "udpValues": {}}
    p2 = parsed(tables=[trow(3, "Cliente", physical="CLIENTE")],
                columns=[crow(3, "Cliente", "A", udp={"UDP Campo Cross": ""}, udp_defaults={"UDP Campo Cross": "Si"})],
                column_udp={"UDP Campo Cross": [CP]})
    plan = build_plan(p2, ctx(schemas=_SCHEMAS, tables=[table], columns_by_table={"t1": [existing]}))
    # existente + celda vacía = conserva: el default del MAPEO no aplica ⇒ unchanged
    assert _cols(plan) == [] and plan.report["summary"]["columns"] == {"create": 0, "update": 0, "unchanged": 1}


def test_columna_nueva_hereda_tipo_logico_del_dominio():
    p = parsed(tables=[trow(3, "Cliente", schema="ddv")], columns=[crow(3, "Cliente", "Codigo Cliente", domain="Codigo")])
    plan = build_plan(p, ctx(schemas=_SCHEMAS, domains=DOMAINS))
    (a,) = _cols(plan)
    assert (a["dataType"], a["logicalDataType"], a["logicalTypeOverridden"]) == ("VARCHAR(30)", "VARCHAR(20)", False)


def test_columna_existente_conserva_campos_de_faceta():
    existing = {"id": "c1", "projectId": "p1", "tableId": "t1", "physicalName": "COD", "logicalName": "Codigo", "parentDomainId": None,
                "dataType": "STRING", "typeOverridden": False, "isPrimaryKey": None,
                "isForeignKey": None, "isNullable": True, "isPartition": False, "description": None, "ordinal": 0,
                "udpValues": {}, "logicalDataType": "VARCHAR(20)", "logicalTypeOverridden": True,
                "logicalOnly": True}
    table = {"id": "t1", "projectId": "p1", "physicalName": "CLIENTE", "logicalName": "Cliente", "schema": "ddv",
             "description": None, "udpValues": {}}
    p = parsed(tables=[trow(3, "Cliente", physical="CLIENTE")],
               columns=[crow(3, "Cliente", "Codigo", physical="COD", description="nueva def")])
    plan = build_plan(p, ctx(schemas=_SCHEMAS, tables=[table], columns_by_table={"t1": [existing]}))
    (a,) = _cols(plan)
    assert (a["logicalDataType"], a["logicalTypeOverridden"], a["logicalOnly"], a["description"]) == ("VARCHAR(20)", True, True, "nueva def")
