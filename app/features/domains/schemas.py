"""DTOs de entrada de `domains`."""
from __future__ import annotations

from pydantic import BaseModel, field_validator

from app.core.datatypes import canonicalize_default_type


class ParentDomainBody(BaseModel):
    name: str
    defaultDataType: str
    # Doc 69: tipo LÓGICO del dominio (faceta Entidad/Atributo); opcional.
    logicalDataType: str | None = None
    namingTerm: str | None = None  # R5b: aditivo (ver ParentDomainDoc).
    description: str | None = None
    # Doc 79: marca "atributo estándar". `bool | None` a propósito: omitido ⇒
    # exclude_none lo descarta ⇒ un update que no lo envíe NO pisa el flag.
    inheritsName: bool | None = None

    # Doc 62: el default se HOMOLOGA a la grafía canónica de la plataforma en
    # el borde (`Array` → `ARRAY<>`, `BIG INTEGER` → `BIGINT`); lo desconocido
    # queda verbatim. Así un dominio jamás vuelve a guardarse des-homologado.
    @field_validator("defaultDataType")
    @classmethod
    def _canonicalize(cls, v: str) -> str:
        return canonicalize_default_type(v)

    # Doc 69: misma homologación para el tipo lógico ('' ⇒ None).
    @field_validator("logicalDataType")
    @classmethod
    def _canonicalize_logical(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return canonicalize_default_type(v) or None
