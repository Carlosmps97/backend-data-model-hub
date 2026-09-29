"""Plantilla built-in «QA_MODELO» (doc 95 D11): el formato fijo que piden los
modeladores — una fila por columna con los datos de su tabla, hoja «QA_MODELO».
Se siembra por proyecto (one-shot y botón «Create QA_MODELO template») y queda
EDITABLE como cualquier otra: es dato, no código. Los UDP van por su nombre
legible (la faceta física es el nombre plano). PURO."""
from __future__ import annotations

BUILTIN_ORIGIN = "builtin:qa-modelo"

QA_MODELO: dict = {
    "name": "QA_MODELO",
    "sheetName": "QA_MODELO",
    "description": "Fixed QA format requested by the modelers: one row per column with its table data and UDPs.",
    # Doc 102: `QA_REPORTE_2026-09-28 153045.xlsx` (hora local de quien exporta).
    "fileName": "QA_REPORTE_{yyyy}-{MM}-{dd} {HH}{mm}{ss}",
    "columns": [
        {"header": "DATABASE", "source": "table.schema"},
        {"header": "TABLA_FISICA", "source": "table.physicalName"},
        {"header": "ORDEN_FISICO", "source": "column.position"},
        {"header": "CAMPO_FISICO", "source": "column.physicalName"},
        {"header": "PK", "source": "column.isPrimaryKey"},
        {"header": "CLASIF_DATO", "source": "column.udp:Clasificacion del Dato"},
        {"header": "PARTICION", "source": "column.udp:Particion"},
        {"header": "PARENT_DOMAIN", "source": "column.parentDomain"},
        {"header": "CAMPO_LOGICO", "source": "column.logicalName"},
        {"header": "DEFINICION", "source": "column.description"},
        {"header": "UDP_TABLA_REFERENCIA", "source": "column.udp:Tabla Referencia"},
        # Supuesto aprobado (doc 95 §7): la clasificación de la TABLA; la de la columna es CLASIF_DATO.
        {"header": "UDP_CLASIFICACION_DEL_DATO", "source": "table.udp:Clasificacion del Dato"},
        {"header": "UDP_CAMPO_CROSS", "source": "column.udp:Campo Cross"},
        {"header": "UDP_DOMINIO_PRINCIPAL", "source": "table.udp:Dominio Principal"},
    ],
}
