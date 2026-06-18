"""API pública de la feature `excel_import`.

`parse_workbook(file_bytes)` → `ExcelPreview`. El `router` se importa directo
desde `app.features.excel_import.router` en el composition root.
"""

from app.features.excel_import.normalizer import NormalizedType, normalize_type
from app.features.excel_import.schemas import ExcelPreview, PreviewColumn, PreviewTable
from app.features.excel_import.service import parse_workbook

__all__ = [
    "ExcelPreview",
    "NormalizedType",
    "PreviewColumn",
    "PreviewTable",
    "normalize_type",
    "parse_workbook",
]
