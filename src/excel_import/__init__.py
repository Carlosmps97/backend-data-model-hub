"""Excel import feature.

Public surface:
- `parse_workbook(file_bytes)` → `ExcelPreview` con todas las tablas y descripciones.
- `normalize_type(raw)` → `NormalizedType` con el tipo canónico + length/scale.

La lógica de cada paso vive en módulos chicos y enfocados:
- `type_normalizer`: limpieza/parseo/normalización de tipos de datos.
- `workbook_reader`: lectura de hojas con openpyxl (sin lógica de negocio).
- `service`:         orquestador que ensambla el `ExcelPreview` listo para devolver al frontend.
- `schemas`:         Pydantic v2 — contrato HTTP del endpoint.
"""

from src.excel_import.schemas import (
    ExcelPreview,
    PreviewColumn,
    PreviewTable,
)
from src.excel_import.service import parse_workbook
from src.excel_import.type_normalizer import NormalizedType, normalize_type

__all__ = [
    "ExcelPreview",
    "NormalizedType",
    "PreviewColumn",
    "PreviewTable",
    "normalize_type",
    "parse_workbook",
]
