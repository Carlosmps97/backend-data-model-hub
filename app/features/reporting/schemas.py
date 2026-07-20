"""DTOs de `reporting`: forma de las filas del reporte (pantalla pr)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ReportTableRow(BaseModel):
    """Una fila de la tabla de Reporting (nivel tabla)."""

    id: str
    physicalName: str | None = None
    logicalName: str | None = None
    sql_schema: str | None = Field(default=None, alias="schema")
    subjectAreas: list[str] = Field(default_factory=list)
    columnCount: int = 0
    relationshipCount: int = 0
    projects: list[str] = Field(default_factory=list)
    description: str | None = None


class ReportColumnRow(BaseModel):
    """Una fila de detalle a nivel columna (export por niveles)."""

    tableId: str | None = None
    physicalName: str | None = None
    logicalName: str | None = None
    dataType: str | None = None
    parentDomain: str | None = None
    isPrimaryKey: bool = False
    isForeignKey: bool = False
    isNullable: bool = True
    isPartition: bool = False
    description: str | None = None
    ordinal: int = 0
