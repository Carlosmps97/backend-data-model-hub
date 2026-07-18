"""Modelo de `schemas` (doc 18): el esquema físico de BD como ENTIDAD
versionada. Las tablas/vistas siguen guardando el string `schema` (sin FK) —
la entidad cataloga los nombres válidos (dropdowns sin typos) y versiona su
ciclo de vida (crear / renombrar con propagación / eliminar) vía changesets."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.core.models import DOC_CONFIG


class SchemaDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str | None = None
