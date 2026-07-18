"""Modelos de `catalog` (tablas y columnas canónicas universales)."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.core.models import DOC_CONFIG


class CanonicalTableDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    physicalName: str
    logicalName: str
    sql_schema: str | None = Field(default=None, alias="schema")
    description: str | None = None
    # UDP: valores de las etiquetas key-value asignadas a esta tabla
    # ({udpDefId: value}). Aditivo (invariante §2.6): las keys se definen en Data
    # Standards; acá viven los valores elegidos para clasificar el dato.
    udpValues: dict[str, str] = Field(default_factory=dict)


class CanonicalColumnDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tableId: str
    physicalName: str
    logicalName: str
    parentDomainId: str | None = None
    dataType: str
    typeOverridden: bool = False
    isPrimaryKey: bool | None = None
    # Posición dentro de la LLAVE primaria (0-based; None si no es PK o si la
    # PK se marcó a mano sin orden). Es INDEPENDIENTE del `ordinal` físico:
    # Erwin ordena el bloque PK del diagrama y el PRIMARY KEY(...) del DDL por
    # el orden de la llave, no por el de las columnas (doc 19 §12b).
    pkPosition: int | None = None
    isForeignKey: bool | None = None
    # Aditivos (invariante §2.6): nulabilidad, columna de partición y
    # descripción (def funcional a nivel columna).
    isNullable: bool = True
    isPartition: bool = False
    description: str | None = None
    ordinal: int = 0
    # UDP: valores de las etiquetas key-value asignadas a esta columna.
    udpValues: dict[str, str] = Field(default_factory=dict)
