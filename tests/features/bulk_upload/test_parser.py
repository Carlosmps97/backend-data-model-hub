"""`header_key` (doc 55 §4 / doc 78 §5): clave tolerante de cabeceras — caso,
espacios, guiones y tildes no importan. La interpretación del workbook se
prueba en `test_profile_apply.py`."""
from __future__ import annotations

from app.features.bulk_upload.parser import ParsedWorkbook, header_key


def test_header_key_tolerante():
    assert header_key("tabla lógico") == "TABLA_LOGICO"
    assert header_key("UDP Campo Cross") == header_key("UDP_Campo_Cross") == header_key("udp-campo-cross") == "UDP_CAMPO_CROSS"
    assert header_key("  Clasificación_del_Dato ") == "CLASIFICACION_DEL_DATO"
    assert header_key(None) == "" and header_key(42) == "42"


def test_parsed_header_devuelve_la_real_o_el_fallback():
    p = ParsedWorkbook(headers={"tables": {"schema": "ESQ"}})
    assert p.header("tables", "schema", "ESQUEMA") == "ESQ"
    assert p.header("tables", "logicalName", "TABLA_LOGICO") == "TABLA_LOGICO"
    assert p.header("columns", "pk", "PK") == "PK"
