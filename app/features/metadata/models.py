"""Modelos Pydantic de la feature `metadata` (catálogo transversal de
Semantic Types y UDPs).

Viven en colecciones globales propias (`semantic_types`, `udps`), keyed por
`_id` sin `projectId`: son metadata de gobierno a nivel organización, asignable
a las tablas/columnas de cualquier proyecto. Espeja `model.ts`.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.core.models import DOC_CONFIG


class SemanticTagDoc(BaseModel):
    """Etiqueta de vocabulario controlado que lleva un Semantic Type. Al asignar
    el tipo a una columna, el usuario elige un valor de `allowedValues`."""

    model_config = DOC_CONFIG

    key: str
    allowedValues: list[str] = Field(default_factory=list)


class SemanticTypeDoc(BaseModel):
    """Clasificación semántica de una columna (p. ej. Monto, Porcentaje). Al
    asignarla, hereda `dataType` a la columna y muestra sus tags controlados."""

    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    dataType: str
    description: str | None = None
    tags: list[SemanticTagDoc] = Field(default_factory=list)


class UdpDoc(BaseModel):
    """Propiedad definida por el usuario, asignable a tablas y/o columnas.
    Extensible: texto libre o enum controlado. `appliesTo` ∈ {'table','column'}."""

    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str | None = None
    appliesTo: list[str] = Field(default_factory=list)
    valueType: str = "text"  # 'text' | 'enum'
    allowedValues: list[str] | None = None
