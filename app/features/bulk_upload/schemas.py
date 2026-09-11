"""DTOs de la carga masiva (doc 78 §5): el front manda TODAS las hojas del
workbook como grillas crudas — `{name, rows: [{row, cells: string[]}]}` — y el
backend aplica el perfil (`profiles/apply`). Los topes son defensivos (413 en
el router): un modelo grande cabe de sobra."""
from __future__ import annotations

from pydantic import BaseModel, Field

MAX_SHEETS = 30
MAX_ROWS_PER_SHEET = 20000
MAX_CELLS_PER_ROW = 200


class RawRow(BaseModel):
    row: int = Field(ge=1)                 # nº de fila Excel (para el reporte)
    cells: list[str] = Field(default_factory=list)


class RawSheet(BaseModel):
    name: str
    rows: list[RawRow] = Field(default_factory=list)


class UploadWorkbookBody(BaseModel):
    fileName: str = "workbook.xlsx"
    profileId: str
    sheets: list[RawSheet] = Field(default_factory=list)
    # Doc 87 §3.5: carpeta raíz «proyecto interno» bajo la que cuelgan SPACE /
    # SUBJECT / DIAGRAMA. Obligatoria solo cuando el proyecto tiene 2+ raíces
    # con subcarpetas (`upload_targets` → mode `choose`); con una sola, el
    # backend la resuelve solo; sin capa, va la raíz del proyecto.
    targetFolderId: str | None = None
