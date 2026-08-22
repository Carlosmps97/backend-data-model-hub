"""DTOs de `catalog`."""
from __future__ import annotations

from pydantic import BaseModel, Field


class CanonicalTableBody(BaseModel):
    logicalName: str
    physicalName: str | None = None
    sql_schema: str | None = Field(default=None, alias="schema")
    description: str | None = None
    udpValues: dict[str, str] | None = None   # etiquetas UDP asignadas a la tabla


class CanonicalColumnBody(BaseModel):
    logicalName: str
    physicalName: str | None = None
    parentDomainId: str | None = None
    # `dataType` opcional = override manual; si falta, se hereda del dominio.
    dataType: str | None = None
    isPrimaryKey: bool | None = None
    isForeignKey: bool | None = None
    isNullable: bool = True
    isPartition: bool = False
    description: str | None = None
    ordinal: int = 0
    udpValues: dict[str, str] | None = None   # etiquetas UDP asignadas a la columna
