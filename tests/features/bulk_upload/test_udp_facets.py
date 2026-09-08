"""Doc 69 §4.4: `UDP_<x>` = faceta física (plantilla intacta); `UDP_LOGICAL_<x>` =
faceta lógica. Los homónimos ya no disparan `ambiguous-udp`. Las columnas
nuevas heredan el tipo LÓGICO del dominio; las existentes conservan sus campos
de faceta."""
from __future__ import annotations

from app.features.bulk_upload.normalize import norm_key, udp_header_facet
from app.features.bulk_upload.planner import build_plan

from .helpers import by_coll, crow, ctx, parsed, trow

_SCHEMAS = [{"id": "s1", "name": "ddv", "kind": "tables"}]
DEFS = [{"id": "c-p", "name": "Clasificacion del Dato", "level": "column", "dataType": "list",
         "allowedValues": ["No Definido", "No DAC", "DAC-NOMBRE"]},
        {"id": "c-l", "name": "Clasificacion del Dato", "level": "column", "view": "logical", "dataType": "list",
         "allowedValues": ["No Definido", "No DAC", "DAC-NOMBRE"]},
        {"id": "x-l", "name": "Atributo Cross", "level": "column", "view": "logical", "dataType": "list",
         "allowedValues": ["No Definido", "Si", "No"]}]
DOMAINS = [{"id": "d1", "name": "Codigo", "defaultDataType": "VARCHAR(30)", "logicalDataType": "VARCHAR(20)"}]


def _cols(plan):
    return [c["payload"] for c in by_coll(plan).get("canonical_columns", [])]


def test_udp_header_facet():
    assert udp_header_facet("UDP_Clasificacion_del_Dato") == ("physical", "clasificacion dato")
    assert udp_header_facet("UDP_LOGICAL_Atributo_Cross") == ("logical", "atributo cross")
    assert udp_header_facet("UDP_LOGICAL_Clasificacion_del_Dato") == ("logical", "clasificacion dato")
    assert norm_key("UDP_Clasificacion_del_Dato") == "clasificacion dato"   # sin cambios


def test_homonimo_por_faceta_no_es_ambiguo_y_cada_cabecera_va_a_su_def():
    p = parsed(tables=[trow(3, "Cliente", schema="ddv")],
               columns=[crow(3, "Cliente", "A", data_type="STRING",
                             udp={"UDP_Clasificacion_del_Dato": "dac-nombre",
                                  "UDP_LOGICAL_Clasificacion_del_Dato": "no dac",
                                  "UDP_LOGICAL_Atributo_Cross": "si"})],
               column_udp_headers=["UDP_Clasificacion_del_Dato", "UDP_LOGICAL_Clasificacion_del_Dato",
                                   "UDP_LOGICAL_Atributo_Cross"])
    plan = build_plan(p, ctx(schemas=_SCHEMAS, udp_defs=DEFS))
    assert plan.has_errors is False
    (a,) = _cols(plan)
    assert a["udpValues"] == {"c-p": "DAC-NOMBRE", "c-l": "No DAC", "x-l": "Si"}


def test_columna_nueva_hereda_tipo_logico_del_dominio():
    p = parsed(tables=[trow(3, "Cliente", schema="ddv")], columns=[crow(3, "Cliente", "Codigo Cliente", domain="Codigo")])
    plan = build_plan(p, ctx(schemas=_SCHEMAS, domains=DOMAINS))
    (a,) = _cols(plan)
    assert (a["dataType"], a["logicalDataType"], a["logicalTypeOverridden"]) == ("VARCHAR(30)", "VARCHAR(20)", False)


def test_columna_existente_conserva_campos_de_faceta():
    existing = {"id": "c1", "projectId": "p1", "tableId": "t1", "physicalName": "COD", "logicalName": "Codigo", "parentDomainId": None,
                "dataType": "STRING", "typeOverridden": False, "isPrimaryKey": None, "pkPosition": None,
                "isForeignKey": None, "isNullable": True, "isPartition": False, "description": None, "ordinal": 0,
                "udpValues": {}, "logicalDataType": "VARCHAR(20)", "logicalTypeOverridden": True,
                "logicalOnly": True}
    table = {"id": "t1", "projectId": "p1", "physicalName": "CLIENTE", "logicalName": "Cliente", "schema": "ddv",
             "description": None, "udpValues": {}, "physicalOnly": True}
    c = ctx(schemas=_SCHEMAS, tables=[table], columns_by_table={"t1": [existing]})
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Codigo", data_type="BIGINT")]), c)
    (col,) = _cols(plan)
    assert (col["logicalDataType"], col["logicalTypeOverridden"], col["logicalOnly"]) == ("VARCHAR(20)", True, True)
    assert "logicalOrdinal" not in col and "columnOrdinal" not in col   # doc 74
