"""Shim de compatibilidad: la gramática de tipos vive en `app/core/datatypes`
(doc 62 — la comparten carga masiva, DTOs de parent domains y kit Erwin).
Este módulo conserva la ruta de import original de doc 55."""
from __future__ import annotations

from app.core.datatypes import (  # noqa: F401
    ARG_SPECS,
    CATALOG,
    canonical_type,
    canonicalize_default_type,
)

__all__ = ["ARG_SPECS", "CATALOG", "canonical_type", "canonicalize_default_type"]
