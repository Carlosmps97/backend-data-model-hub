"""DTOs de `dictionary`."""
from __future__ import annotations

from pydantic import BaseModel


class AbbreviationBody(BaseModel):
    term: str
    abbrev: str
    scope: str = "column"  # 'column' | 'table'
    wordType: str | None = None  # 'prime' | 'class' | 'modifier'


class PhysicalizeBody(BaseModel):
    logical: str
    # Opcional (compat): si se omite, scope='column'. Resuelve separador+case
    # desde naming_config y usa SOLO términos de ese scope.
    scope: str | None = None
    # Override explícito del separador (precede a la config). El frontend lo usa
    # para previews ad-hoc; si se pasa, gana sobre naming_config[scope].separator.
    separator: str | None = None


class LogicalizeBody(BaseModel):
    physical: str


class RephysicalizeBody(BaseModel):
    # R5: re-physicalize retroactivo. Sin scope ⇒ ambos ('table' y 'column').
    scope: str | None = None
