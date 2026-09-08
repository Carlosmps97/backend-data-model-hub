"""Doc 69 §4.4: homónimos por faceta no colisionan en el export (la física
conserva el nombre plano; la lógica lleva «(Logical)») + tipo lógico en filas."""
from __future__ import annotations

from app.features.reporting.service import column_rows, table_rows

DEFS = [{"id": "u-p", "name": "Clasificacion del Dato", "level": "table"},
        {"id": "u-l", "name": "Clasificacion del Dato", "level": "table", "view": "logical"},
        {"id": "c-p", "name": "Clasificacion del Dato", "level": "column"},
        {"id": "c-l", "name": "Atributo Cross", "level": "column", "view": "logical"}]


def test_table_rows_traduce_udp_por_faceta():
    rows = table_rows([{"id": "t1", "physicalName": "T", "logicalName": "t",
                        "udpValues": {"u-p": "DAC", "u-l": "No DAC"}}],
                      {}, [], [], [], udp_defs=DEFS)
    assert rows[0]["udpValues"] == {"Clasificacion del Dato": "DAC", "Clasificacion del Dato (Logical)": "No DAC"}


def test_column_rows_traduce_udp_por_faceta_y_expone_tipo_logico():
    rows = column_rows([{"tableId": "t1", "physicalName": "C", "logicalName": "c", "dataType": "VARCHAR(30)",
                         "logicalDataType": "VARCHAR(20)", "ordinal": 0,
                         "udpValues": {"c-p": "DAC-NOMBRE", "c-l": "Si"}}], [], udp_defs=DEFS)
    assert rows[0]["udpValues"] == {"Clasificacion del Dato": "DAC-NOMBRE", "Atributo Cross (Logical)": "Si"}
    assert rows[0]["logicalDataType"] == "VARCHAR(20)"
