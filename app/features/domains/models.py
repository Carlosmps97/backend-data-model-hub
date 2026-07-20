"""Modelos de la feature `domains` (Parent Domains: tipo por defecto + cascada)."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.core.models import DOC_CONFIG


class ParentDomainDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    defaultDataType: str
    # R5b: término de naming sugerido por el dominio (lo muestra el diseño p4).
    # Aditivo (invariante de persistencia): el seed ya lo siembra y antes se
    # descartaba al leer por `extra="ignore"`. Default None = no-breaking.
    namingTerm: str | None = None
    description: str | None = None
