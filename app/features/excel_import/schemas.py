"""Pydantic schemas para la respuesta del endpoint de preview.

Los nombres de los campos están en camelCase para encajar sin
transformación con el frontend TypeScript (mismo patrón que el resto
del backend — ver `db_models.py`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


_cfg = ConfigDict(extra="forbid", populate_by_name=True)


MatchKind = Literal["exact", "alias", "fuzzy", "unknown", "empty"]


class PreviewColumn(BaseModel):
    """Una columna ya normalizada lista para mostrarse en el preview."""

    model_config = _cfg

    name: str
    #: Tipo canónico (`decimal`, `varchar`, ...). El frontend lo persiste
    #: tal cual en `TableColumn.dataType` cuando el usuario confirma.
    dataType: str
    #: Cadena original tal como la escribió el usuario en el Excel
    #: (`"decimal(10,2)"`, `"decximam"`). Útil para mostrar de dónde
    #: salió la normalización.
    rawDataType: str
    length: int | None = None
    scale: int | None = None
    functionalDefinition: str | None = None
    typeConfidence: float
    typeMatchedVia: MatchKind


class PreviewTable(BaseModel):
    """Una tabla parseada desde una hoja del workbook."""

    model_config = _cfg

    #: Nombre completo de la hoja (`"dbo.Customer"` o `"Customer"`).
    sheetName: str
    #: Schema parseado del nombre de la hoja (lo de antes del `.`), o `None`.
    #: Se expone como `schema` en JSON; el sufijo `_` evita chocar con
    #: `BaseModel.schema()` (deprecado pero todavía expuesto en Pydantic v2).
    schema_: str | None = Field(default=None, alias="schema")
    #: Nombre de la tabla (lo de después del `.`, o el nombre completo si no había `.`).
    name: str
    #: Descripción de la tabla tomada de la hoja `TablesDescriptions`, o `None`.
    description: str | None = None
    columns: list[PreviewColumn]


class ExcelPreview(BaseModel):
    """Respuesta del endpoint `POST /api/excel-import/preview`."""

    model_config = _cfg

    tables: list[PreviewTable]
    #: True si encontramos una hoja `TablesDescriptions` en el archivo.
    tableDescriptionsFound: bool
    #: Avisos no fatales (hojas vacías, headers raros, descripciones huérfanas, ...).
    warnings: list[str]
