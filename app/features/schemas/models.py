"""Modelo de `schemas` (doc 18): el esquema físico de BD como ENTIDAD
versionada. Las tablas/vistas siguen guardando el string `schema` (sin FK) —
la entidad cataloga los nombres válidos (dropdowns sin typos) y versiona su
ciclo de vida (crear / renombrar con propagación / eliminar) vía changesets."""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.core.models import DOC_CONFIG


class SchemaDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str | None = None
    # doc 44: qué contiene el esquema — 'tables' | 'views'. Erwin NO trae este
    # dato (los Hive_Database del XML no llevan ningún atributo/UDP que lo
    # marque), así que nace acá: la migración lo deriva de los MIEMBROS y la
    # UI lo pide al crear. Opcional: docs pre-doc-44 siguen validando.
    kind: Literal["tables", "views"] | None = None
