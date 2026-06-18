"""Modelos Pydantic de la feature `projects`.

Un **Project** posee `engines[]`, `layers: ModelLevel[]` (los "Modelos /
niveles", p. ej. RDV·UDV·DDV) y `domains: Domain[]` (productos de datos
verticales que cruzan capas). Esos arrays chicos van embebidos en el documento
`projects`. Espeja `web-data-model-hub/src/types/model.ts`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.models import DOC_CONFIG, TagDoc, coerce_tags


class ModelLevelDoc(BaseModel):
    """Un "Modelo (nivel)" — capa agnóstica del proyecto (p. ej. RDV/UDV/DDV).
    Espeja el TS `ModelLevel`. Embebido en el documento del proyecto."""

    model_config = DOC_CONFIG

    id: str
    name: str                       # código corto, p. ej. "RDV"
    full: str | None = None         # nombre completo, p. ej. "Raw Data Vault"
    color: str | None = None
    order: int = 0                  # posición izq→der + adyacencia en el canvas
    engine: str | None = None
    desc: str | None = None


class DomainDoc(BaseModel):
    """Un Dominio (producto de datos) — a nivel proyecto y VERTICAL (cruza capas).
    Espeja el TS `Domain`. Embebido en el documento del proyecto."""

    model_config = DOC_CONFIG

    id: str
    name: str
    color: str | None = None
    owner: str | None = None
    steward: str | None = None
    # 'Public' | 'Internal' | 'Confidential' | 'Restricted' | 'PII' — se guarda
    # como str para resiliencia ante valores legacy / escritos por el agente.
    sensitivity: str | None = None
    description: str | None = None
    subdomains: list[str] = Field(default_factory=list)
    layers: list[str] = Field(default_factory=list)  # ids de ModelLevel cubiertos
    # Tags de gobierno key/value (Feature 2). Se coercionan como TableDoc.tags.
    tags: list[TagDoc] | None = None

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_legacy_tags(cls, v: Any) -> Any:
        return coerce_tags(v)


class ProjectDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str
    name: str
    description: str | None = None
    engines: list[str] = Field(default_factory=list)
    layers: list[ModelLevelDoc] = Field(default_factory=list)
    domains: list[DomainDoc] = Field(default_factory=list)
    createdAt: str
    updatedAt: str
