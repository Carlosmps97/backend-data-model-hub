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
    isForeignKey: bool | None = None
    # Aditivos (invariante §2.6): nulabilidad, columna de partición y
    # descripción (def funcional a nivel columna).
    isNullable: bool = True
    isPartition: bool = False
    description: str | None = None
    ordinal: int = 0
