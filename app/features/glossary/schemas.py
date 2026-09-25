"""DTOs de `dictionary`."""
from __future__ import annotations

from pydantic import BaseModel, Field


class AbbreviationBody(BaseModel):
    term: str
    abbrev: str
    scope: str = "column"  # 'column' | 'table'


class PhysicalizeBody(BaseModel):
    logical: str
    # Opcional (compat): si se omite, scope='column'. Resuelve separador+case
    # desde naming_config y usa SOLO términos de ese scope.
    scope: str | None = None
    # Override explícito del separador (precede a la config). El frontend lo usa
    # para previews ad-hoc; si se pasa, gana sobre naming_config[scope].separator.
    separator: str | None = None


class RephysicalizeBody(BaseModel):
    # R5: re-physicalize retroactivo. Sin scope ⇒ ambos ('table' y 'column').
    scope: str | None = None


class ValidateTermBody(BaseModel):
    # F2 #1: validación on-demand de un término/frase antes de agregarlo.
    term: str
    scope: str = "column"  # 'column' | 'table'


class ImpactTerm(BaseModel):
    id: str | None = None             # None = término nuevo
    term: str
    abbrev: str


class ImpactNaming(BaseModel):
    separator: str | None = None
    case: str | None = None


class ImpactBody(BaseModel):
    """Doc 94 D7: el borrador del glosario de UN scope, para el dry-run."""
    scope: str = "column"             # 'column' | 'table'
    termsUpsert: list[ImpactTerm] = Field(default_factory=list)
    termsDelete: list[str] = Field(default_factory=list)
    namingConfig: ImpactNaming | None = None
