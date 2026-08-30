"""DTOs de la carga masiva (doc 55 §7).

El front parsea el `.xlsx` y manda, por hoja, la fila de cabeceras y las
filas de datos como `{row: nº de fila Excel, cells: {CABECERA: texto}}`. El
backend es quien interpreta las cabeceras (parser) — el front no conoce la
semántica de la plantilla.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# Topes defensivos (413 en el router): un modelo grande cabe de sobra; más
# allá, el body y el plan en memoria dejan de ser razonables por request.
MAX_TABLE_ROWS = 5000
MAX_COLUMN_ROWS = 20000


class SheetRow(BaseModel):
    row: int = Field(ge=1)              # nº de fila en el Excel (para el reporte)
    cells: dict[str, str] = Field(default_factory=dict)


class Sheet(BaseModel):
    headers: list[str] = Field(default_factory=list)
    rows: list[SheetRow] = Field(default_factory=list)


class UploadSheets(BaseModel):
    tables: Sheet | None = None         # hoja `Tablas`
    columns: Sheet | None = None        # hoja `Atributos`


class UploadWorkbookBody(BaseModel):
    fileName: str = "workbook.xlsx"
    sheets: UploadSheets
