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
    # Doc 88 §7: subjectAreas = carpetas de los canvases; diagrams = los canvases.
    diagrams: list[str] = Field(default_factory=list)
    spaces: list[str] = Field(default_factory=list)   # doc 102: carpeta raíz (sub-proyecto)
    columnCount: int = 0
    relationshipCount: int = 0
    projects: list[str] = Field(default_factory=list)
    udpValues: dict[str, str] = Field(default_factory=dict)   # {nombre de la def: valor}
    description: str | None = None
    # Doc 70 §4.2 — metadata completa (docs 68/69).
    physicalNameOverridden: bool = False
    logicalOnly: bool = False
    physicalOnly: bool = False


class ReportFilterOptions(BaseModel):
    """Doc 70 §4.1 — universo de los filtros del reporte tabular."""

    schemas: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    subjectAreas: list[str] = Field(default_factory=list)


class ReportColumnRow(BaseModel):
    """Una fila de detalle a nivel columna (export por niveles)."""

    id: str | None = None
    tableId: str | None = None
    physicalName: str | None = None
    logicalName: str | None = None
    dataType: str | None = None
    logicalDataType: str | None = None       # doc 69: faceta lógica
    parentDomain: str | None = None
    parentDomainId: str | None = None
    isPrimaryKey: bool = False
    isForeignKey: bool = False
    isNullable: bool = True
    isPartition: bool = False
    udpValues: dict[str, str] = Field(default_factory=dict)
    description: str | None = None
    physicalDescription: str | None = None   # doc 85: comment físico (Erwin Comment)
    ordinal: int = 0
    # Doc 70 §4.2 — metadata completa (docs 68/69; doc 94 retiró pkPosition).
    typeOverridden: bool = False
    logicalTypeOverridden: bool = False
    physicalNameOverridden: bool = False
    logicalOnly: bool = False
    physicalOnly: bool = False


# Doc 102 (431): tope de ids por pedido en el CUERPO (el front manda lotes de
# 300 tablas para columnas y 2 000 para vistas).
MAX_BATCH_IDS = 5000


class ReportBatchBody(BaseModel):
    """Doc 102: un lote de tablas con los ids en el CUERPO — en la URL, cientos
    de ids pasaban el tope de 16 KB de cabeceras del servidor del front (431)."""

    projectId: str = Field(min_length=1)
    tableIds: list[str] = Field(min_length=1, max_length=MAX_BATCH_IDS)
    changesetId: str | None = None
