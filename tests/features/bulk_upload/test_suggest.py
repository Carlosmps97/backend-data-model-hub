"""Doc 78 §3.3: el match por nombre (norm_key) es una SUGERENCIA del editor:
alias conocidos → field; nombre de UDP → todas las facetas que coincidan;
sin match → ignore."""
from __future__ import annotations

from app.features.bulk_upload.profiles.suggest import suggest

DEFS = [{"id": "t-p", "name": "Tipo de Entidad", "level": "table", "view": "physical"},
        {"id": "t-l", "name": "Tipo de Entidad", "level": "table", "view": "logical"},
        {"id": "c-p", "name": "Clasificación del Dato", "level": "column", "view": "physical"},
        {"id": "x-l", "name": "Atributo Cross", "level": "column", "view": "logical"}]


def test_campos_por_alias_y_udp_por_nombre_en_ambas_facetas():
    out = suggest("tables", ["TABLA_LOGICO", "Tabla Física", "DEF_TABLA", "udp tipo de entidad", "Clasificacion_del_Dato", "LOGICO"], DEFS)
    by = {o["header"]: o for o in out}
    assert by["TABLA_LOGICO"]["target"] == {"kind": "field", "field": "logicalName"} and by["TABLA_LOGICO"]["matched"] == "field"
    assert by["Tabla Física"]["target"]["field"] == "physicalName"
    assert by["DEF_TABLA"]["target"]["field"] == "description"
    assert by["udp tipo de entidad"]["target"] == {"kind": "udp", "udpIds": ["t-p", "t-l"]} and by["udp tipo de entidad"]["matched"] == "udp"
    assert by["Clasificacion_del_Dato"] == {"header": "Clasificacion_del_Dato", "target": {"kind": "ignore"}, "matched": None}  # es de columna, no de tabla
    assert by["LOGICO"]["matched"] is None


def test_columnas_alias_ingles_y_udp_de_columna():
    out = suggest("columns", ["Table", "Column name", "Data type", "Parent Domain", "PK", "UDP_Clasificacion_del_Dato", "Atributo Cross"], DEFS)
    fields = [o["target"].get("field") for o in out]
    assert fields[:5] == ["tableRef", "logicalName", "dataType", "parentDomain", "pk"]
    assert out[5]["target"] == {"kind": "udp", "udpIds": ["c-p"]}
    assert out[6]["target"] == {"kind": "udp", "udpIds": ["x-l"]}
